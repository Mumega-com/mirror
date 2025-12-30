
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
    'latent_dim': 128,
    'num_layers': 4,
    'epochs_per_task': 5,
    'batch_size': 128,
    'base_lr': 0.002,
    'tau': 50,
    'gate_k': 3.0,                # High sensitivity
    'gate_min': 0.0,              # Allow complete freezing
    'norm_window': 100,
    'n_tasks': 5,
    'dt': 0.1
}

# ==============================================================================
# LAMBDA-TENSOR RESONATOR MODEL (FRC 840) - HOLOGRAPHIC READOUT
# WITH FREQUENCY-GATED PLASTICITY
# ==============================================================================

class ResonatorBlock(nn.Module):
    def __init__(self, dim=128):
        super().__init__()
        self.dim = dim
        self.kappa = nn.Parameter(torch.randn(dim, dim) * 0.05) 
        self.omega = nn.Parameter(torch.randn(dim) * 0.1)
        
    def forward(self, x, dt=0.1):
        # x: [batch, dim] (phases)
        diff = x.unsqueeze(2) - x.unsqueeze(1) 
        coupling = torch.sin(diff) * self.kappa
        oscillator_update = coupling.sum(dim=2)
        new_x = x + dt * (self.omega + oscillator_update)
        new_x = (new_x + torch.pi) % (2 * torch.pi) - torch.pi
        return new_x

class LambdaResonatorNet(nn.Module):
    def __init__(self, input_dim=784, latent_dim=128, num_layers=4, output_size=10):
        super().__init__()
        self.encoder = nn.Linear(input_dim, latent_dim)
        self.layers = nn.ModuleList([ResonatorBlock(latent_dim) for _ in range(num_layers)])
        self.decoder = nn.Linear(latent_dim * 2, output_size)
        
    def forward(self, x):
        z = self.encoder(x.view(x.size(0), -1))
        for layer in self.layers:
            z = layer(z, dt=CONFIG['dt'])
        z_holo = torch.cat([torch.sin(z), torch.cos(z)], dim=1)
        return self.decoder(z_holo)

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
        """Negative Entropy Proxy"""
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

def compute_gate(alpha, k=2.0, gate_min=0.0):
    """
    alpha > 0 (Rising Coherence) -> Gate -> 0 (Freeze)
    alpha < 0 (Dropping Coherence) -> Gate -> 1 (Learn)
    """
    gate = 1.0 / (1.0 + np.exp(k * alpha))
    return max(gate, gate_min)

# ==============================================================================
# DATA AND TRAINING
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

def train_cgl_frequency_gated(model, tasks):
    """
    V3: Frequency-Gated Plasticity
    - Omega (Natural Frequencies) are 'Memory' (harder to update).
    - Kappa (Couplings) are 'Context' (easier to update).
    - Both modulated by Alpha Drift.
    """
    criterion = nn.CrossEntropyLoss()
    monitor = CoherenceMonitor(tau=CONFIG['tau'])
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    # Separate parameter groups
    omega_params = [p for n, p in model.named_parameters() if 'omega' in n]
    kappa_params = [p for n, p in model.named_parameters() if 'kappa' in n]
    other_params = [p for n, p in model.named_parameters() if 'omega' not in n and 'kappa' not in n]
    
    optimizer = optim.Adam([
        {'params': omega_params, 'lr': CONFIG['base_lr']}, 
        {'params': kappa_params, 'lr': CONFIG['base_lr']},
        {'params': other_params, 'lr': CONFIG['base_lr']}
    ])
    
    for task_id, task in enumerate(tasks):
        print(f"\nTask {task_id} (CGL-FreqGated)...")
        model.train()
        
        for epoch in range(CONFIG['epochs_per_task']):
            for x, y in task['train']:
                x, y = x.to(device), y.to(device)
                
                # 1. Forward
                logits = model(x)
                loss = criterion(logits, y)
                
                # 2. Compute Gate
                alpha = monitor.update(logits)
                gate = compute_gate(alpha, k=CONFIG['gate_k'], gate_min=CONFIG['gate_min'])
                
                # 3. Backward
                optimizer.zero_grad()
                loss.backward()
                
                # 4. FREQUENCY-GATED PLASTICITY UPDATE
                # Rule: 
                #   - Omega (Memory): Very sensitive to gate. Freeze aggressively.
                #     lr_scale = gate^2 (Converges to 0 faster)
                #   - Kappa (Context): Linearly sensitive.
                #     lr_scale = gate
                #   - Other (Encoder/Decoder): Pass through but scaled.
                
                with torch.no_grad():
                    # Apply gate to Omega Gradients (Memory Protection)
                    for p in omega_params:
                        if p.grad is not None:
                            p.grad *= (gate * gate) # Aggressive protection
                    
                    # Apply gate to Kappa Gradients (Context Adaptation)
                    for p in kappa_params:
                        if p.grad is not None:
                            p.grad *= gate
                            
                    # Apply gate to others
                    for p in other_params:
                        if p.grad is not None:
                            p.grad *= gate
                
                optimizer.step()
                
        # Evaluate
        for eval_id, eval_task in enumerate(tasks):
            acc_matrix[task_id, eval_id] = evaluate(model, eval_task['test'])
        print(f"  Accs: {[f'{a*100:.1f}%' for a in acc_matrix[task_id]]}")
        
    return acc_matrix, monitor.history

# ==============================================================================
# RUN
# ==============================================================================

print("="*60)
print("EXPERIMENT: ALPHA-RESONATOR V3 (Freq-Gated Plasticity)")
print("Logic: Omega params freeze by gate^2, Kappa by gate.")
print("="*60)

model = LambdaResonatorNet(latent_dim=CONFIG['latent_dim'], num_layers=CONFIG['num_layers']).to(device)
acc_cgl, history = train_cgl_frequency_gated(model, tasks)

# Metrics
n = acc_cgl.shape[0]
aa = acc_cgl[-1].mean()
forgetting = 0
bwt = 0
for j in range(n - 1):
    peak_acc = acc_cgl[j:n-1, j].max()
    final_acc = acc_cgl[-1, j]
    forgetting += peak_acc - final_acc
    bwt += final_acc - acc_cgl[j, j]

avg_forg = forgetting / (n-1)
avg_bwt = bwt / (n-1)

print("\n" + "="*60)
print("FINAL RESULTS - V3")
print("="*60)
print(f"Average Accuracy (AA): {aa*100:.2f}%")
print(f"Backward Transfer (BWT): {avg_bwt*100:.2f}%")
print(f"Forgetting (Forg): {avg_forg*100:.2f}%")
print("="*60)
