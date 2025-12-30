
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
from copy import deepcopy

# ==============================================================================
# CHIMERA V10 - DEFINITIVE ABLATION STUDY
# ==============================================================================

CONFIG = {
    'batch_size': 64,
    'latent_dim': 1024,
    'spectral_radius': 0.95,
    'input_scale': 1.0, 
    'dt': 0.2,                    
    'integration_steps': 10,
    
    'buffer_size_per_task': 200,   # CONSTANT BUDGET
    'replay_batch_size': 32,
    
    'lr': 0.001,
    'epochs_per_task': 3,
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
# SHARED INFRASTRUCTURE
# ==============================================================================

class ReplayBuffer:
    def __init__(self, capacity_per_task, store_state=False):
        self.capacity = capacity_per_task
        self.store_state = store_state
        self.buffer = {} 
        
    def add_task_data(self, task_id, dataset, model=None):
        indices = list(range(len(dataset)))
        random.shuffle(indices)
        selected = indices[:self.capacity]
        
        data = []
        with torch.no_grad():
            for i in selected:
                x, y = dataset[i]
                if self.store_state and model:
                    # PROJECT TO STATE (h)
                    x = x.unsqueeze(0).to(device)
                    h = model.get_state(x).cpu() # [1, latent_dim]
                    data.append((h, y))
                else:
                    # STORE PIXEL (x)
                    data.append((x, y))
        
        self.buffer[task_id] = data
        print(f"Stored {len(data)} samples for Task {task_id} (State={self.store_state})")
        
    def sample(self, batch_size):
        all_samples = []
        for task_id in self.buffer:
            all_samples.extend(self.buffer[task_id])
        if len(all_samples) == 0:
            return None, None
        batch = random.choices(all_samples, k=batch_size)
        x_batch = torch.stack([b[0] for b in batch])
        if self.store_state:
             x_batch = x_batch.squeeze(1) # [B, latent]
        y_batch = torch.tensor([b[1] for b in batch])
        return x_batch, y_batch

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
        
        train_subset = Subset(train_data, train_idx)
        # Note: We need accessible dataset for buffer
        
        train_loader = DataLoader(train_subset, batch_size=CONFIG['batch_size'], shuffle=True)
        test_loader = DataLoader(Subset(test_data, test_idx), batch_size=CONFIG['batch_size'])
        
        tasks.append({
            'train_loader': train_loader, 
            'test_loader': test_loader, 
            'train_subset': train_subset,
            'digits': digits
        })
    return tasks

# ==============================================================================
# MODEL 1: MLP BASELINE (Comparable Parameter Count)
# ==============================================================================

class MLPBaseline(nn.Module):
    def __init__(self):
        super().__init__()
        # Matches Reservoir Dim
        self.fc1 = nn.Linear(784, CONFIG['latent_dim'])
        self.fc2 = nn.Linear(CONFIG['latent_dim'], 10)
        self.relu = nn.ReLU()
        
    def forward(self, x):
        x = x.view(x.size(0), -1)
        x = self.relu(self.fc1(x))
        return self.fc2(x)

# ==============================================================================
# MODEL 2 & 3: CHIMERA (Reservoir)
# ==============================================================================

class ChimeraNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.latent_dim = CONFIG['latent_dim']
        
        self.encoder = nn.Linear(784, self.latent_dim, bias=False)
        self.encoder.weight.requires_grad = False
        nn.init.orthogonal_(self.encoder.weight)
        
        # Stable Reservoir
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
        
    def get_state(self, x):
        """Returns the final reservoir state h (before readout)"""
        u_flat = x.view(x.size(0), -1)
        u_in = self.encoder(u_flat)
        h = torch.zeros(x.size(0), self.latent_dim).to(x.device)
        
        # Dynamics
        for _ in range(CONFIG['integration_steps']):
             drive = torch.mm(h, self.W_res) + torch.mm(u_in, self.W_in) + self.bias
             dh = -h + self.tanh(drive)
             h = h + CONFIG['dt'] * dh
            
        h = self.norm(h)
        return h

    def forward_from_state(self, h):
        """Skip dynamics, just readout"""
        return self.readout(h)

    def forward(self, x):
        h = self.get_state(x)
        return self.readout(h)

# ==============================================================================
# TRAINER
# ==============================================================================

def train_ablation(model, tasks, mode='pixel_replay', name='Model'):
    print(f"\n[{name}] Starting Training (Mode: {mode})")
    
    # Setup optimizer - only trained params
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = optim.Adam(params, lr=CONFIG['lr'])
    criterion = nn.CrossEntropyLoss()
    
    # Store State?
    store_state = (mode == 'state_replay')
    replay_buffer = ReplayBuffer(CONFIG['buffer_size_per_task'], store_state=store_state)
    
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    for task_id, task in enumerate(tasks):
        print(f"  Task {task_id}...")
        
        for epoch in range(CONFIG['epochs_per_task']):
            model.train()
            
            for x, y in task['train_loader']:
                x, y = x.to(device), y.to(device)
                
                # 1. Forward Current
                if mode == 'state_replay':
                     # Need to compute h for current batch
                     h_curr = model.get_state(x)
                     logits = model.forward_from_state(h_curr)
                else:
                     logits = model(x)
                
                loss = criterion(logits, y)
                
                # 2. Replay Mixing
                x_rep, y_rep = replay_buffer.sample(CONFIG['replay_batch_size'])
                if x_rep is not None:
                    x_rep, y_rep = x_rep.to(device), y_rep.to(device)
                    
                    if mode == 'state_replay':
                        # x_rep is actually h
                        logits_rep = model.forward_from_state(x_rep)
                    else:
                        # x_rep is pixel
                        logits_rep = model(x_rep)
                        
                    loss_rep = criterion(logits_rep, y_rep)
                    loss = (loss + loss_rep) / 2.0
                
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
        
        # Add to buffer
        replay_buffer.add_task_data(task_id, task['train_subset'], model if store_state else None)
        
        # Evaluate
        for eval_id in range(n_tasks):
            acc_matrix[task_id, eval_id] = evaluate(model, tasks[eval_id]['test_loader'])
            
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
    bwt = 0
    cnt = n - 1
    for j in range(cnt):
        peak = acc_matrix[j, j]
        final = acc_matrix[-1, j]
        forgetting += (peak - final)
        bwt += (final - peak)
    return aa, forgetting/cnt, bwt/cnt

# ==============================================================================
# MAIN ABLATION RUN
# ==============================================================================

if __name__ == "__main__":
    tasks = get_split_mnist()
    
    print("="*60)
    print("CHIMERA V10: 3-WAY ABLATION")
    print("="*60)
    
    # 1. MLP Baseline
    mlp = MLPBaseline().to(device)
    acc_mlp = train_ablation(mlp, tasks, mode='pixel_replay', name='MLP + Pixel Replay')
    
    # 2. Chimera Pixel
    chimera_pix = ChimeraNet().to(device)
    acc_pix = train_ablation(chimera_pix, tasks, mode='pixel_replay', name='Reservoir + Pixel Replay')
    
    # 3. Chimera State
    chimera_state = ChimeraNet().to(device)
    acc_state = train_ablation(chimera_state, tasks, mode='state_replay', name='Reservoir + State Replay')
    
    # Metrics
    aa_mlp, forg_mlp, bwt_mlp = compute_metrics(acc_mlp)
    aa_pix, forg_pix, bwt_pix = compute_metrics(acc_pix)
    aa_state, forg_state, bwt_state = compute_metrics(acc_state)
    
    print("\n" + "="*60)
    print("FINAL ABLATION RESULTS")
    print("="*60)
    print(f"{'Method':<30} | {'AA':<8} | {'Forg':<8} | {'BWT':<8}")
    print("-" * 60)
    print(f"{'MLP + Pixel Replay':<30} | {aa_mlp*100:5.1f}% | {forg_mlp*100:5.1f}% | {bwt_mlp*100:5.1f}%")
    print(f"{'Reservoir + Pixel Replay':<30} | {aa_pix*100:5.1f}% | {forg_pix*100:5.1f}% | {bwt_pix*100:5.1f}%")
    print(f"{'Reservoir + State Replay':<30} | {aa_state*100:5.1f}% | {forg_state*100:5.1f}% | {bwt_state*100:5.1f}%")
    print("="*60)
