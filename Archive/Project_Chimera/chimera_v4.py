
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms
import numpy as np
import matplotlib.pyplot as plt
import os

# ==============================================================================
# PROJECT CHIMERA - CONFIGURATION
# ==============================================================================

CONFIG = {
    'batch_size': 64,             # Smaller batch for dynamics
    'latent_dim': 512,            # Reservoir needs space
    'reservoir_layers': 2,        # Depth of the tank
    'spectral_radius': 1.5,       # Chaos tuning
    'input_scale': 0.5,           # Input injection strength
    'dt': 0.05,                   # Time step
    'integration_steps': 10,      # Steps per forward pass
    
    'lr_readout': 0.005,          # Fast learning for readout
    'lr_reservoir': 0.0001,       # Very slow/frozen for reservoir
    
    'epochs_per_task': 3,         # Quick adaptation
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

def get_split_mnist():
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])
    try:
        train_data = datasets.MNIST('./data', train=True, download=True, transform=transform)
        test_data = datasets.MNIST('./data', train=False, download=True, transform=transform)
    except:
        # Fallback if download fails (use existing folder if possible)
        train_data = datasets.MNIST('../scratch/data', train=True, download=True, transform=transform)
        test_data = datasets.MNIST('../scratch/data', train=False, download=True, transform=transform)

    tasks = []
    print(f"Constructing {CONFIG['n_tasks']} tasks...")
    for task_id in range(CONFIG['n_tasks']):
        digits = [task_id * 2, task_id * 2 + 1]
        
        train_idx = [i for i, (_, y) in enumerate(train_data) if y in digits]
        test_idx = [i for i, (_, y) in enumerate(test_data) if y in digits]
        
        train_loader = DataLoader(Subset(train_data, train_idx), 
                                  batch_size=CONFIG['batch_size'], shuffle=True)
        test_loader = DataLoader(Subset(test_data, test_idx), 
                                 batch_size=CONFIG['batch_size'])
        
        tasks.append({'train': train_loader, 'test': test_loader, 'digits': digits})
        print(f"  Task {task_id}: Digits {digits} | Train: {len(train_idx)} | Test: {len(test_idx)}")
    
    return tasks

# ==============================================================================
# FRC 840.003 - CHIMERA RESERVOIR NETWORK
# ==============================================================================

class ChimeraBlock(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim
        self.tanh = nn.Tanh()
        
        # Reservoir Weights (Recurrent)
        # Initialize with spectral radius for "Edge of Chaos"
        W_res = torch.randn(dim, dim)
        u, s, v = torch.svd(W_res)
        s[0] = CONFIG['spectral_radius'] # Force largest singular value
        W_res = torch.mm(torch.mm(u, torch.diag(s)), v.t())
        self.W_res = nn.Parameter(W_res, requires_grad=True) # Allow micro-tuning
        
        # Input Injection Weights
        self.W_in = nn.Parameter(torch.randn(dim, dim) * CONFIG['input_scale'], requires_grad=False) # Fixed
        
        # Bias
        self.bias = nn.Parameter(torch.randn(dim) * 0.1, requires_grad=False)

    def forward(self, h, u_in):
        # h: current state [batch, dim]
        # u_in: input injection [batch, dim]
        
        # Continuous-time update (Runge-Kutta 4 or Euler)
        # Euler for speed: dh/dt = -h + tanh(W_res*h + W_in*u + b)
        
        drive = torch.mm(h, self.W_res) + torch.mm(u_in, self.W_in) + self.bias
        dh = -h + self.tanh(drive)
        
        return h + CONFIG['dt'] * dh

class ChimeraNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.latent_dim = CONFIG['latent_dim']
        
        # 1. FIXED ENCODER (The "Eye")
        # Projects 784 -> 512. Random, Frozen.
        self.encoder = nn.Linear(784, self.latent_dim, bias=False)
        self.encoder.weight.requires_grad = False
        nn.init.orthogonal_(self.encoder.weight)
        
        # 2. RESERVOIR (The "Brain")
        self.reservoir = ChimeraBlock(self.latent_dim)
        
        # 3. READOUT (The "Mouth")
        # Learns to map reservoir state to digits
        self.readout = nn.Linear(self.latent_dim, 10)
        
    def forward(self, x):
        # x: [batch, 1, 28, 28] -> [batch, 784]
        u_flat = x.view(x.size(0), -1)
        
        # Project to latent input
        u_in = self.encoder(u_flat)
        
        # Initialize state (zero)
        h = torch.zeros(x.size(0), self.latent_dim).to(x.device)
        
        # "Spin up" the reservoir dynamics
        # We inject the SAME static image input for T steps
        # This simulates the brain processing a static visual stimulus over time
        for _ in range(CONFIG['integration_steps']):
            h = self.reservoir(h, u_in)
            
        # Readout from final state
        return self.readout(h)

# ==============================================================================
# COHERENCE & GATE (FRC 841)
# ==============================================================================

def compute_coherence(logits):
    # Coherence = Negative Entropy
    probs = torch.softmax(logits, dim=-1)
    entropy = -torch.sum(probs * torch.log(probs + 1e-8), dim=-1)
    return -entropy.mean().item()

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

def train_chimera(tasks):
    print("\nInitializing Project Chimera...")
    model = ChimeraNet().to(device)
    
    # Differential Learning Rates
    # Readout learns fast. Reservoir learns almost nothing (just drifts).
    optimizer = optim.Adam([
        {'params': model.readout.parameters(), 'lr': CONFIG['lr_readout']},
        {'params': model.reservoir.parameters(), 'lr': CONFIG['lr_reservoir']}
    ])
    
    criterion = nn.CrossEntropyLoss()
    
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    coherence_history = []
    
    for task_id, task in enumerate(tasks):
        print(f"\n>>> Task {task_id} (Chimera Mode) <<<")
        
        for epoch in range(CONFIG['epochs_per_task']):
            model.train()
            epoch_loss = 0
            
            for x, y in task['train']:
                x, y = x.to(device), y.to(device)
                
                optimizer.zero_grad()
                logits = model(x)
                loss = criterion(logits, y)
                loss.backward()
                
                # Gradient Clipping for stability
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                
                optimizer.step()
                epoch_loss += loss.item()
                
                # Track coherence
                coherence_history.append(compute_coherence(logits))

            # Quick check
            print(f"  Epoch {epoch+1}/{CONFIG['epochs_per_task']} | Loss: {epoch_loss/len(task['train']):.4f}")

        # Evaluate on ALL tasks seen so far
        print("  Evaluating memory...")
        for eval_id in range(n_tasks):
            acc = evaluate(model, tasks[eval_id]['test'])
            acc_matrix[task_id, eval_id] = acc
            # visual marker for current vs past
            marker = "current" if eval_id == task_id else ("past" if eval_id < task_id else "future")
            if eval_id <= task_id:
                print(f"    Task {eval_id} ({marker}): {acc*100:.1f}%")
    
    return acc_matrix, coherence_history

# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    if not os.path.exists('./data'):
        os.makedirs('./data')
        
    tasks = get_split_mnist()
    acc_matrix, coh_hist = train_chimera(tasks)
    
    # Final Metrics
    final_accs = acc_matrix[-1]
    aa = final_accs.mean()
    
    bwt = 0
    forgetting = 0
    cnt = 0
    for j in range(CONFIG['n_tasks'] - 1):
        peak = acc_matrix[j, j] # Accuracy right after training
        final = acc_matrix[-1, j] # Accuracy at the end
        forgetting += (peak - final)
        bwt += (final - peak)
        cnt += 1
    
    avg_forg = forgetting / cnt
    avg_bwt = bwt / cnt
    
    print("\n" + "="*60)
    print("PROJECT CHIMERA: FINAL REPORT")
    print("="*60)
    print(f"Average Accuracy (AA): {aa*100:.2f}%")
    print(f"Forgetting (Forg):     {avg_forg*100:.2f}%")
    print(f"Backward Transfer:     {avg_bwt*100:.2f}%")
    print("-" * 60)
    
    # Plot matrix
    plt.figure(figsize=(8, 6))
    plt.imshow(acc_matrix * 100, cmap='RdYlGn', vmin=0, vmax=100)
    plt.colorbar(label='Accuracy')
    plt.title(f'Chimera Memory Matrix (Forg={avg_forg*100:.1f}%)')
    plt.xlabel('Task')
    plt.ylabel('Training Stage')
    for i in range(5):
        for j in range(5):
            v = acc_matrix[i, j] * 100
            plt.text(j, i, f'{v:.0f}', ha='center', va='center', color='black')
    plt.savefig('chimera_results.png')
    print("Saved matrix to chimera_results.png")
