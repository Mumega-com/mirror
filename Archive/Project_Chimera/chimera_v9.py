
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
# PROJECT CHIMERA V9 - STABLE RESERVOIR + REPLAY
# ==============================================================================

CONFIG = {
    'batch_size': 64,
    'latent_dim': 1024,
    'spectral_radius': 0.95,      # Stable regime (<1.0)
    'input_scale': 1.0, 
    'dt': 0.2,                    # Larger steps, faster dynamics
    'integration_steps': 10,
    
    'buffer_size_per_task': 200,  # Larger buffer (approx 1000 samples total)
    'replay_batch_size': 32,      # More replay
    
    'lr_readout': 0.001,          # Lower LR for stability
    'lr_reservoir': 0.0,
    
    'epochs_per_task': 5,         # More time to converge
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
# DATA & REPLAY BUFFER
# ==============================================================================

class ReplayBuffer:
    def __init__(self, capacity_per_task):
        self.capacity = capacity_per_task
        self.buffer = {} 
        
    def add_task_data(self, task_id, dataset):
        indices = list(range(len(dataset)))
        random.shuffle(indices)
        selected = indices[:self.capacity]
        data = []
        for i in selected:
            data.append(dataset[i])
        self.buffer[task_id] = data
        print(f"Stored {len(data)} samples for Task {task_id}")
        
    def sample(self, batch_size):
        all_samples = []
        for task_id in self.buffer:
            all_samples.extend(self.buffer[task_id])
        if len(all_samples) == 0:
            return None, None
        batch = random.choices(all_samples, k=batch_size)
        x_batch = torch.stack([b[0] for b in batch])
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
        test_subset = Subset(test_data, test_idx)
        
        train_loader = DataLoader(train_subset, batch_size=CONFIG['batch_size'], shuffle=True)
        test_loader = DataLoader(test_subset, batch_size=CONFIG['batch_size'])
        
        tasks.append({
            'train_loader': train_loader, 
            'test_loader': test_loader, 
            'train_dataset': train_subset,
            'digits': digits
        })
    return tasks

# ==============================================================================
# CHIMERA NET V9
# ==============================================================================

class StableChimeraBlock(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
        self.tanh = nn.Tanh()
        
        # Dense SVD initialization for stability
        W_res = torch.randn(dim, dim)
        u, s, v = torch.svd(W_res)
        s[0] = CONFIG['spectral_radius']
        W_res = torch.mm(torch.mm(u, torch.diag(s)), v.t())
        self.W_res = nn.Parameter(W_res, requires_grad=False) 
        
        self.W_in = nn.Parameter(torch.randn(dim, dim) * CONFIG['input_scale'], requires_grad=False)
        self.bias = nn.Parameter(torch.randn(dim) * 0.1, requires_grad=False)

    def forward(self, h, u_in):
        drive = torch.mm(h, self.W_res) + torch.mm(u_in, self.W_in) + self.bias
        dh = -h + self.tanh(drive)
        return h + CONFIG['dt'] * dh

class ChimeraNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.latent_dim = CONFIG['latent_dim']
        
        self.encoder = nn.Linear(784, self.latent_dim, bias=False)
        self.encoder.weight.requires_grad = False
        nn.init.orthogonal_(self.encoder.weight)
        
        self.reservoir = StableChimeraBlock(self.latent_dim)
        
        # Norm layer for stability before readout
        self.norm = nn.LayerNorm(self.latent_dim)
        
        self.readout = nn.Linear(self.latent_dim, 10)
        
    def forward(self, x):
        u_flat = x.view(x.size(0), -1)
        u_in = self.encoder(u_flat)
        h = torch.zeros(x.size(0), self.latent_dim).to(x.device)
        
        for _ in range(CONFIG['integration_steps']):
            h = self.reservoir(h, u_in)
            
        h = self.norm(h) # Stabilize features
        return self.readout(h)

# ==============================================================================
# TRAINING LOOP
# ==============================================================================

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

def train_chimera_v9(tasks):
    print("\nInitializing Project Chimera V9 (Stable Replay)...")
    model = ChimeraNet().to(device)
    optimizer = optim.Adam(model.readout.parameters(), lr=CONFIG['lr_readout'])
    criterion = nn.CrossEntropyLoss()
    
    replay_buffer = ReplayBuffer(CONFIG['buffer_size_per_task'])
    
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    for task_id, task in enumerate(tasks):
        print(f"\n>>> Task {task_id} (V9 Replay) <<<")
        
        for epoch in range(CONFIG['epochs_per_task']):
            model.train()
            epoch_loss = 0
            
            for x, y in task['train_loader']:
                x, y = x.to(device), y.to(device)
                
                # REPLAY MIXING
                x_replay, y_replay = replay_buffer.sample(CONFIG['replay_batch_size'])
                if x_replay is not None:
                    x_replay, y_replay = x_replay.to(device), y_replay.to(device)
                    x = torch.cat([x, x_replay], dim=0)
                    y = torch.cat([y, y_replay], dim=0)
                
                optimizer.zero_grad()
                logits = model(x)
                loss = criterion(logits, y)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()

            print(f"  Epoch {epoch+1}/{CONFIG['epochs_per_task']} | Loss: {epoch_loss:.4f}")
        
        replay_buffer.add_task_data(task_id, task['train_dataset'])

        print("  Evaluating memory...")
        for eval_id in range(n_tasks):
            acc = evaluate(model, tasks[eval_id]['test_loader'])
            acc_matrix[task_id, eval_id] = acc
            marker = "current" if eval_id == task_id else ("past" if eval_id < task_id else "future")
            if eval_id <= task_id:
                print(f"    Task {eval_id} ({marker}): {acc*100:.1f}%")
    
    return acc_matrix

if __name__ == "__main__":
    tasks = get_split_mnist()
    acc_matrix = train_chimera_v9(tasks)
    
    final_accs = acc_matrix[-1]
    aa = final_accs.mean()
    bwt = 0
    forgetting = 0
    cnt = 0
    for j in range(CONFIG['n_tasks'] - 1):
        peak = acc_matrix[j, j]
        final = acc_matrix[-1, j]
        forgetting += (peak - final)
        bwt += (final - peak)
        cnt += 1
        
    avg_forg = forgetting / cnt
    avg_bwt = bwt / cnt
    
    print("\n" + "="*60)
    print("PROJECT CHIMERA V9: FINAL REPORT")
    print("="*60)
    print(f"Average Accuracy (AA): {aa*100:.2f}%")
    print(f"Forgetting (Forg):     {avg_forg*100:.2f}%")
    print(f"Backward Transfer:     {avg_bwt*100:.2f}%")
    print("-" * 60)
    
    plt.figure(figsize=(8, 6))
    plt.imshow(acc_matrix * 100, cmap='RdYlGn', vmin=0, vmax=100)
    plt.colorbar(label='Accuracy')
    plt.title(f'Chimera V9 (Stable) Matrix (Forg={avg_forg*100:.1f}%)')
    plt.xlabel('Task')
    plt.ylabel('Training Stage')
    for i in range(5):
        for j in range(5):
            v = acc_matrix[i, j] * 100
            plt.text(j, i, f'{v:.0f}', ha='center', va='center', color='black')
    plt.savefig('chimera_v9_results.png')
    print("Saved matrix to chimera_v9_results.png")
