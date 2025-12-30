
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms
import numpy as np
import matplotlib.pyplot as plt
import os
import random

# ==============================================================================
# CHIMERA V11 - ONLINE LEARNING (BATCH SIZE 1)
# ==============================================================================

CONFIG = {
    'batch_size': 1,              # ONLINE STREAMING
    'latent_dim': 1024,
    'spectral_radius': 0.95,
    'input_scale': 1.0, 
    'dt': 0.2,                    
    'integration_steps': 10,
    
    'buffer_size_per_task': 50,   # Tiny Buffer
    'replay_batch_size': 1,       # 1 Current + 1 Replay
    
    'lr': 0.001,
    'epochs_per_task': 1,         # SINGLE PASS
    'n_tasks': 5,
    'seed': 42
}

torch.manual_seed(CONFIG['seed'])
np.random.seed(CONFIG['seed'])

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"
print(f"Device: {device}")

# ==============================================================================
# DATA: SPLIT-MNIST
# ==============================================================================

class ReplayBuffer:
    def __init__(self, capacity_per_task):
        self.capacity = capacity_per_task
        self.buffer = {} 
        
    def add_sample(self, task_id, x, y):
        # Reservoir Sampling for online stream
        if task_id not in self.buffer:
            self.buffer[task_id] = []
        
        if len(self.buffer[task_id]) < self.capacity:
            self.buffer[task_id].append((x.cpu(), y.cpu()))
        else:
            # Replace random element (Reservoir Sampling)
            idx = random.randint(0, self.capacity-1)
            self.buffer[task_id][idx] = (x.cpu(), y.cpu())
            
    def sample(self):
        all_samples = []
        for task_id in self.buffer:
            all_samples.extend(self.buffer[task_id])
        if len(all_samples) == 0:
            return None, None
        
        # Pick 1 random sample
        sample = random.choice(all_samples)
        return sample[0].to(device), sample[1].to(device)

def get_split_mnist():
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])
    try:
        train_data = datasets.MNIST('./data', train=True, download=True, transform=transform)
        test_data = datasets.MNIST('./data', train=False, download=True, transform=transform)
    except:
        train_data = datasets.MNIST('../scratch/data', train=True, download=True, transform=transform)
        test_data = datasets.MNIST('../scratch/data', train=False, download=True, transform=transform)

    tasks = []
    print(f"Constructing {CONFIG['n_tasks']} tasks...")
    for task_id in range(CONFIG['n_tasks']):
        digits = [task_id * 2, task_id * 2 + 1]
        
        train_idx = [i for i, (_, y) in enumerate(train_data) if y in digits]
        test_idx = [i for i, (_, y) in enumerate(test_data) if y in digits]
        
        # Limit training data to 1000 samples per task for speed in online mode
        train_idx = train_idx[:1000]
        
        train_loader = DataLoader(Subset(train_data, train_idx), 
                                  batch_size=1, shuffle=True) # Batch Size 1
        
        test_loader = DataLoader(Subset(test_data, test_idx), 
                                 batch_size=128) # Test can be batched
        
        tasks.append({'train': train_loader, 'test': test_loader, 'digits': digits})
        print(f"  Task {task_id}: Digits {digits} | Stream Length: {len(train_idx)}")
    
    return tasks

# ==============================================================================
# MODELS
# ==============================================================================

class MLPBaseline(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(784, CONFIG['latent_dim'])
        self.fc2 = nn.Linear(CONFIG['latent_dim'], 10)
        self.relu = nn.ReLU()
    def forward(self, x):
        x = x.view(x.size(0), -1)
        x = self.relu(self.fc1(x))
        return self.fc2(x)

class ChimeraNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.latent_dim = CONFIG['latent_dim']
        
        self.encoder = nn.Linear(784, self.latent_dim, bias=False)
        self.encoder.weight.requires_grad = False
        nn.init.orthogonal_(self.encoder.weight)
        
        W_res = torch.randn(self.latent_dim, self.latent_dim)
        u, s, v = torch.svd(W_res)
        s[0] = CONFIG['spectral_radius']
        W_res = torch.mm(torch.mm(u, torch.diag(s)), v.t())
        self.W_res = nn.Parameter(W_res, requires_grad=False) 
        
        self.W_in = nn.Parameter(torch.randn(self.latent_dim, self.latent_dim) * CONFIG['input_scale'], requires_grad=False)
        self.bias = nn.Parameter(torch.randn(self.latent_dim) * 0.1, requires_grad=False)
        
        self.norm = nn.LayerNorm(self.latent_dim)
        self.tanh = nn.Tanh()
        self.readout = nn.Linear(self.latent_dim, 10)
        
    def forward(self, x):
        u_flat = x.view(x.size(0), -1)
        u_in = self.encoder(u_flat)
        h = torch.zeros(x.size(0), self.latent_dim).to(x.device)
        for _ in range(CONFIG['integration_steps']):
             drive = torch.mm(h, self.W_res) + torch.mm(u_in, self.W_in) + self.bias
             dh = -h + self.tanh(drive)
             h = h + CONFIG['dt'] * dh
        h = self.norm(h)
        return self.readout(h)

# ==============================================================================
# TRAINING LOOP - ONLINE
# ==============================================================================

def train_online(model, tasks, name='Model'):
    print(f"\n[{name}] Starting Online Stream...")
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = optim.Adam(params, lr=0.001) # Adam is standard for online
    criterion = nn.CrossEntropyLoss()
    replay_buffer = ReplayBuffer(CONFIG['buffer_size_per_task'])
    
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    for task_id, task in enumerate(tasks):
        print(f"  Task {task_id} Stream...", end="")
        model.train()
        
        correct_stream = 0
        total_stream = 0
        
        for i, (x, y) in enumerate(task['train']):
            x, y = x.to(device), y.to(device)
            
            # Interleave Replay (1:1)
            x_rep, y_rep = replay_buffer.sample()
            
            loss = 0
            # 1. Learn Current
            optimizer.zero_grad()
            logits = model(x)
            loss_curr = criterion(logits, y)
            
            # Track stream accuracy
            if logits.argmax().item() == y.item():
                correct_stream += 1
            total_stream += 1
            
            # 2. Replay
            if x_rep is not None:
                logits_rep = model(x_rep.unsqueeze(0)) # Add batch dim [1, 784]
                loss_rep = criterion(logits_rep, y_rep.unsqueeze(0))
                loss = loss_curr + loss_rep
            else:
                loss = loss_curr
                
            loss.backward()
            optimizer.step()
            
            # Add to buffer (reservoir sampling)
            replay_buffer.add_sample(task_id, x.squeeze(0), y.squeeze(0))
            
            if i % 200 == 0: print(".", end="", flush=True)
            
        print(f" Done. Stream Acc: {correct_stream/total_stream*100:.1f}%")
        
        # Evaluate
        for eval_id in range(n_tasks):
            acc_matrix[task_id, eval_id] = evaluate(model, tasks[eval_id]['test'])
            
    return acc_matrix

def evaluate(model, loader):
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            pred = model(x).argmax(dim=1)
            correct += (pred == y).sum().item()
            total += len(y)
    return correct / total if total > 0 else 0

def compute_metrics(acc_matrix):
    n = acc_matrix.shape[0]
    aa = acc_matrix[-1].mean()
    forgetting = 0
    cnt = n - 1
    for j in range(cnt):
        peak = acc_matrix[j, j]
        final = acc_matrix[-1, j]
        forgetting += (peak - final)
    return aa, forgetting/cnt

if __name__ == "__main__":
    tasks = get_split_mnist()
    
    print("="*60)
    print("CHIMERA V11: ONLINE LEARNING BATTLE")
    print("="*60)
    
    # 1. MLP
    mlp = MLPBaseline().to(device)
    acc_mlp = train_online(mlp, tasks, name='MLP Baseline')
    
    # 2. Chimera
    chimera = ChimeraNet().to(device)
    acc_chimera = train_online(chimera, tasks, name='Chimera Net')
    
    aa_mlp, forg_mlp = compute_metrics(acc_mlp)
    aa_chimera, forg_chimera = compute_metrics(acc_chimera)
    
    print("\n" + "="*60)
    print("ONLINE RESULTS (Batch=1, Epoch=1)")
    print("="*60)
    print(f"{'Method':<20} | {'AA':<8} | {'Forg':<8}")
    print("-" * 60)
    print(f"{'MLP Baseline':<20} | {aa_mlp*100:5.1f}% | {forg_mlp*100:5.1f}%")
    print(f"{'Chimera Net':<20} | {aa_chimera*100:5.1f}% | {forg_chimera*100:5.1f}%")
    print("="*60)
