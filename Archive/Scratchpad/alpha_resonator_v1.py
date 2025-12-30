
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms
import numpy as np
import matplotlib.pyplot as plt
from copy import deepcopy

torch.manual_seed(42)
np.random.seed(42)

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"
print(f"Device: {device}")

# ==============================================================================
# CONFIGURATION
# ==============================================================================

CONFIG = {
    'latent_dim': 64,
    'num_layers': 4,
    'epochs_per_task': 10,
    'batch_size': 128,
    'base_lr': 0.001,
    'tau': 50,
    'gate_k': 2.0,
    'gate_min': 0.05,
    'norm_window': 100,
    'n_tasks': 5
}

# ==============================================================================
# LAMBDA-TENSOR RESONATOR MODEL (FRC 840)
# ==============================================================================

class ResonatorBlock(nn.Module):
    def __init__(self, dim=64):
        super().__init__()
        self.dim = dim
        self.kappa = nn.Parameter(torch.randn(dim, dim) * 0.01)
        self.omega = nn.Parameter(torch.randn(dim) * 0.1)
        
    def forward(self, x, dt=0.1):
        # x: [batch, dim]
        diff = x.unsqueeze(2) - x.unsqueeze(1) # [batch, dim, dim]
        coupling = torch.sin(diff) * self.kappa
        oscillator_update = coupling.sum(dim=2)
        new_x = x + dt * (self.omega + oscillator_update)
        # Phase wrapping to maintain geometric stability
        new_x = (new_x + torch.pi) % (2 * torch.pi) - torch.pi
        return new_x

class LambdaResonatorNet(nn.Module):
    def __init__(self, input_dim=784, latent_dim=64, num_layers=4, output_size=10):
        super().__init__()
        self.encoder = nn.Linear(input_dim, latent_dim)
        self.layers = nn.ModuleList([ResonatorBlock(latent_dim) for _ in range(num_layers)])
        self.decoder = nn.Linear(latent_dim, output_size)
        
    def forward(self, x):
        z = self.encoder(x.view(x.size(0), -1))
        for layer in self.layers:
            z = layer(z)
        return self.decoder(z)

# ==============================================================================
# COHERENCE MONITOR (FRC 841)
# ==============================================================================

class CoherenceMonitor:
    def __init__(self, tau=50, norm_window=100):
        self.tau = tau
        self.norm_window = norm_window
        self.ema = None
        self.prev_ema = None
        self.raw_alpha_history = []
        self.history = {'coherence': [], 'ema': [], 'alpha': [], 'gate': []}
    
    def compute_coherence(self, logits):
        with torch.no_grad():
            probs = torch.softmax(logits, dim=-1)
            entropy = -torch.sum(probs * torch.log(probs + 1e-8), dim=-1)
            return -entropy.mean().item()
    
    def update(self, logits):
        C = self.compute_coherence(logits)
        if self.ema is None:
            self.ema = C
            raw_alpha = 0.0
        else:
            self.prev_ema = self.ema
            self.ema = (1 - 1/self.tau) * self.ema + (1/self.tau) * C
            raw_alpha = self.ema - self.prev_ema
        
        self.raw_alpha_history.append(raw_alpha)
        if len(self.raw_alpha_history) >= 10:
            window = self.raw_alpha_history[-self.norm_window:]
            std = np.std(window) + 1e-8
            mean = np.mean(window)
            normalized_alpha = (raw_alpha - mean) / std
        else:
            normalized_alpha = raw_alpha
            
        self.history['coherence'].append(C)
        self.history['ema'].append(self.ema)
        self.history['alpha'].append(normalized_alpha)
        return normalized_alpha

def compute_gate(alpha, k=2.0, gate_min=0.05):
    gate = 1.0 / (1.0 + np.exp(k * alpha))
    return max(gate, gate_min)

# ==============================================================================
# DATA: SPLIT-MNIST
# ==============================================================================

def get_split_mnist():
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])
    train_data = datasets.MNIST('./data', train=True, download=True, transform=transform)
    test_data = datasets.MNIST('./data', train=False, download=True, transform=transform)
    
    tasks = []
    for task_id in range(5):
        digits = [task_id * 2, task_id * 2 + 1]
        train_idx = [i for i, (_, y) in enumerate(train_data) if y in digits]
        test_idx = [i for i, (_, y) in enumerate(test_data) if y in digits]
        
        train_loader = DataLoader(Subset(train_data, train_idx), 
                                  batch_size=CONFIG['batch_size'], shuffle=True)
        test_loader = DataLoader(Subset(test_data, test_idx), 
                                 batch_size=CONFIG['batch_size'])
        tasks.append({'train': train_loader, 'test': test_loader, 'digits': digits})
    return tasks

tasks = get_split_mnist()

# ==============================================================================
# TRAINING FUNCTIONS
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

def train_baseline(model, tasks, name="SGD"):
    optimizer = optim.Adam(model.parameters(), lr=CONFIG['base_lr'])
    criterion = nn.CrossEntropyLoss()
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    for task_id, task in enumerate(tasks):
        print(f"\nTask {task_id} ({name})...")
        model.train()
        for epoch in range(CONFIG['epochs_per_task']):
            for x, y in task['train']:
                x, y = x.to(device), y.to(device)
                optimizer.zero_grad()
                loss = criterion(model(x), y)
                loss.backward()
                optimizer.step()
        for eval_id, eval_task in enumerate(tasks):
            acc_matrix[task_id, eval_id] = evaluate(model, eval_task['test'])
        print(f"  Accs: {[f'{a*100:.1f}%' for a in acc_matrix[task_id]]}")
    return acc_matrix

def train_cgl_resonator(model, tasks):
    optimizer = optim.Adam(model.parameters(), lr=CONFIG['base_lr'])
    criterion = nn.CrossEntropyLoss()
    monitor = CoherenceMonitor(tau=CONFIG['tau'])
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    for task_id, task in enumerate(tasks):
        print(f"\nTask {task_id} (CGL-Resonator)...")
        model.train()
        for epoch in range(CONFIG['epochs_per_task']):
            for x, y in task['train']:
                x, y = x.to(device), y.to(device)
                logits = model(x)
                loss = criterion(logits, y)
                alpha = monitor.update(logits)
                gate = compute_gate(alpha, k=CONFIG['gate_k'], gate_min=CONFIG['gate_min'])
                optimizer.zero_grad()
                (loss * gate).backward()
                optimizer.step()
        for eval_id, eval_task in enumerate(tasks):
            acc_matrix[task_id, eval_id] = evaluate(model, eval_task['test'])
        print(f"  Accs: {[f'{a*100:.1f}%' for a in acc_matrix[task_id]]}")
    return acc_matrix, monitor.history

# ==============================================================================
# RUN
# ==============================================================================

print("="*60)
print("EXPERIMENT: ALPHA-RESONATOR V1")
print("="*60)

# 1. Baseline SGD with Resonator Architecture
print("\nRUNNING RESONATOR BASELINE (SGD)")
model_base = LambdaResonatorNet(latent_dim=CONFIG['latent_dim'], num_layers=CONFIG['num_layers']).to(device)
acc_base = train_baseline(model_base, tasks, name="Resonator-SGD")

# 2. CGL with Resonator Architecture
print("\nRUNNING CGL-RESONATOR")
model_cgl = LambdaResonatorNet(latent_dim=CONFIG['latent_dim'], num_layers=CONFIG['num_layers']).to(device)
acc_cgl, history = train_cgl_resonator(model_cgl, tasks)

# ==============================================================================
# METRICS
# ==============================================================================

def compute_cl_metrics(acc_matrix):
    n = acc_matrix.shape[0]
    aa = acc_matrix[-1].mean()
    forgetting = 0
    bwt = 0
    for j in range(n - 1):
        peak_acc = acc_matrix[j:n-1, j].max()
        final_acc = acc_matrix[-1, j]
        forgetting += peak_acc - final_acc
        bwt += final_acc - acc_matrix[j, j]
    return aa, forgetting/(n-1), bwt/(n-1)

aa_base, forg_base, bwt_base = compute_cl_metrics(acc_base)
aa_cgl, forg_cgl, bwt_cgl = compute_cl_metrics(acc_cgl)

print("\n" + "="*60)
print("FINAL RESULTS")
print("="*60)
print(f"{'Method':<25} | {'AA ↑':<10} | {'BWT ↑':<10} | {'Forg ↓':<10}")
print("-" * 65)
print(f"{'Resonator-SGD':<25} | {aa_base*100:6.2f}% | {bwt_base*100:6.2f}% | {forg_base*100:6.2f}%")
print(f"{'CGL-Resonator':<25} | {aa_cgl*100:6.2f}% | {bwt_cgl*100:6.2f}% | {forg_cgl*100:6.2f}%")
print("-" * 65)
print("✅ Done!")
