
import torch
import torch.nn as nn
import torch.optim as optim
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
# CONFIGURATION - TUNED FOR MNIST
# ==============================================================================

CONFIG = {
    'hidden_sizes': [256, 128],
    'epochs_per_task': 10,
    'batch_size': 128,
    'base_lr': 0.001,
    'tau': 50,                    # Coherence EMA window
    'gate_k': 5.0,                # Sigmoid steepness (tuned for normalized alpha)
    'gate_min': 0.0,              # Allow gate to go to zero
    'use_hard_gating': True,      # Skip updates if gate < threshold
    'gate_threshold': 0.2,        # Threshold for hard gating
    'n_tasks': 5,
    'alpha_norm_window': 200,     # Window for z-score normalization
    'buffer_size': 500,           # Samples to keep from previous tasks
    'replay_batch_size': 32,
    'replay_lambda': 1.0          # Weight of replay loss
}

# ==============================================================================
# MODEL
# ==============================================================================

class MLP(nn.Module):
    def __init__(self, input_size=784, hidden_sizes=[256, 128], output_size=10):
        super().__init__()
        layers = []
        prev = input_size
        for h in hidden_sizes:
            layers.extend([nn.Linear(prev, h), nn.ReLU(), nn.Dropout(0.2)])
            prev = h
        layers.append(nn.Linear(prev, output_size))
        self.net = nn.Sequential(*layers)
    
    def forward(self, x):
        return self.net(x.view(x.size(0), -1))

# ==============================================================================
# COHERENCE MONITOR - IMPROVED
# ==============================================================================

class CoherenceMonitor:
    def __init__(self, tau=50, norm_window=200):
        self.tau = tau
        self.norm_window = norm_window
        self.ema = None
        self.prev_ema = None
        self.alpha_buffer = []
        self.history = {
            'coherence': [], 
            'ema': [], 
            'alpha_raw': [], 
            'alpha_norm': [], 
            'gate': []
        }
    
    def compute_coherence(self, logits):
        """Use Negative Entropy as coherence proxy (canonical FRC)"""
        with torch.no_grad():
            probs = torch.softmax(logits, dim=-1)
            entropy = -torch.sum(probs * torch.log(probs + 1e-8), dim=-1)
            return -entropy.mean().item()  # Negative entropy = coherence
    
    def update(self, logits):
        C = self.compute_coherence(logits)
        
        if self.ema is None:
            self.ema = C
            alpha_raw = 0.0
        else:
            self.prev_ema = self.ema
            self.ema = (1 - 1/self.tau) * self.ema + (1/self.tau) * C
            alpha_raw = self.ema - self.prev_ema
        
        # Track raw alpha for normalization
        self.alpha_buffer.append(alpha_raw)
        if len(self.alpha_buffer) > self.norm_window:
            self.alpha_buffer.pop(0)
            
        # Compute normalized alpha (Z-score)
        if len(self.alpha_buffer) > 10:
            mu = np.mean(self.alpha_buffer)
            std = np.std(self.alpha_buffer) + 1e-8
            alpha_norm = (alpha_raw - mu) / std
        else:
            alpha_norm = 0.0
        
        self.history['coherence'].append(C)
        self.history['ema'].append(self.ema)
        self.history['alpha_raw'].append(alpha_raw)
        self.history['alpha_norm'].append(alpha_norm)
        
        return alpha_norm


class BalancedReplayBuffer:
    def __init__(self, capacity=500):
        self.capacity = capacity
        self.buffer = {} # task_id -> list of (x, y)
    
    def add(self, x, y, task_id):
        """Add samples for a specific task. Balance across tasks."""
        if task_id not in self.buffer:
            self.buffer[task_id] = []
        
        # Add samples (simple reservoir-like for the current task)
        x_cpu = x.detach().cpu()
        y_cpu = y.detach().cpu()
        for i in range(len(x_cpu)):
            if len(self.buffer[task_id]) < self.capacity // 5: # Assuming n_tasks=5
                self.buffer[task_id].append((x_cpu[i], y_cpu[i]))
            else:
                idx = np.random.randint(len(self.buffer[task_id]))
                self.buffer[task_id][idx] = (x_cpu[i], y_cpu[i])
                    
    def sample(self, batch_size):
        if not self.buffer:
            return None, None
        
        all_samples = []
        for tid in self.buffer:
            all_samples.extend(self.buffer[tid])
            
        indices = np.random.choice(len(all_samples), min(batch_size, len(all_samples)), replace=False)
        samples = [all_samples[i] for i in indices]
        
        bx = torch.stack([s[0] for s in samples]).to(device)
        by = torch.stack([s[1] for s in samples]).to(device)
        return bx, by

def compute_gate(alpha_norm, k=5.0, gate_min=0.0):
    """Sigmoid gating on normalized alpha drift"""
    gate = 1.0 / (1.0 + np.exp(k * alpha_norm))
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
        print(f"Task {task_id}: digits {digits}, train={len(train_idx)}, test={len(test_idx)}")
    
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


def train_sgd(model, tasks):
    """Standard SGD baseline with Balanced Experience Replay"""
    optimizer = optim.Adam(model.parameters(), lr=CONFIG['base_lr'])
    criterion = nn.CrossEntropyLoss()
    buffer = BalancedReplayBuffer(capacity=CONFIG['buffer_size'])
    
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    
    for task_id, task in enumerate(tasks):
        print(f"\nTask {task_id} (SGD + Replay)...")
        model.train()
        
        for epoch in range(CONFIG['epochs_per_task']):
            for x, y in task['train']:
                x, y = x.to(device), y.to(device)
                
                # Replay
                rx, ry = buffer.sample(CONFIG['replay_batch_size'])
                replay_loss = 0.0
                if rx is not None:
                    replay_loss = criterion(model(rx), ry)
                
                # Main loss
                optimizer.zero_grad()
                loss = criterion(model(x), y)
                (loss + CONFIG['replay_lambda'] * replay_loss).backward()
                optimizer.step()
                
                # Populate buffer
                buffer.add(x, y, task_id)
        
        # Evaluate on all tasks
        for eval_id, eval_task in enumerate(tasks):
            acc_matrix[task_id, eval_id] = evaluate(model, eval_task['test'])
        
        print(f"  Accs: {[f'{a*100:.1f}%' for a in acc_matrix[task_id]]}")
    
    return acc_matrix


def train_cgl(model, tasks):
    """Coherence-Gated Learning with Selective Memory Replay"""
    optimizer = optim.Adam(model.parameters(), lr=CONFIG['base_lr'])
    criterion = nn.CrossEntropyLoss()
    monitor = CoherenceMonitor(tau=CONFIG['tau'], norm_window=CONFIG['alpha_norm_window'])
    buffer = BalancedReplayBuffer(capacity=CONFIG['buffer_size'])
    
    n_tasks = len(tasks)
    acc_matrix = np.zeros((n_tasks, n_tasks))
    all_gates = []
    
    total_steps = 0
    updates_performed = 0
    
    for task_id, task in enumerate(tasks):
        print(f"\nTask {task_id} (CGL + Selective Replay)...")
        model.train()
        
        for epoch in range(CONFIG['epochs_per_task']):
            for x, y in task['train']:
                x, y = x.to(device), y.to(device)
                total_steps += 1
                
                # Forward
                logits = model(x)
                loss = criterion(logits, y)
                
                # Compute alpha and gate
                alpha_norm = monitor.update(logits)
                gate = compute_gate(alpha_norm, k=CONFIG['gate_k'], gate_min=CONFIG['gate_min'])
                all_gates.append(gate)
                monitor.history['gate'].append(gate)
                
                # Backward Logic - Homeostatic Balance
                optimizer.zero_grad()
                
                rx, ry = buffer.sample(CONFIG['replay_batch_size'])
                replay_loss = 0.0
                if rx is not None:
                    replay_loss = criterion(model(rx), ry)

                if CONFIG['use_hard_gating']:
                    if gate >= CONFIG['gate_threshold']:
                        # Plasticity: Learn New + Maintain Past
                        (loss + CONFIG['replay_lambda'] * replay_loss).backward()
                        optimizer.step()
                        updates_performed += 1
                    else:
                        # Stability: Only Maintain Past (Replay)
                        # This skips dL_new/dtheta but computes dL_replay/dtheta
                        if rx is not None:
                            (CONFIG['replay_lambda'] * replay_loss).backward()
                            optimizer.step()
                else:
                    # Soft gating
                    (loss * gate + CONFIG['replay_lambda'] * replay_loss).backward()
                    optimizer.step()
                    updates_performed += 1
                
                # Populate buffer
                buffer.add(x, y, task_id)
        
        # Evaluate on all tasks
        for eval_id, eval_task in enumerate(tasks):
            acc_matrix[task_id, eval_id] = evaluate(model, eval_task['test'])
        
        print(f"  Accs: {[f'{a*100:.1f}%' for a in acc_matrix[task_id]]}")
    
    print(f"\n  [Efficiency] Updates performed: {updates_performed}/{total_steps} ({updates_performed/total_steps*100:.1f}%)")
    
    return acc_matrix, monitor.history, all_gates, updates_performed/total_steps

# ==============================================================================
# RUN EXPERIMENTS
# ==============================================================================

print("="*60)
print("RUNNING SGD BASELINE")
print("="*60)
model_sgd = MLP(hidden_sizes=CONFIG['hidden_sizes']).to(device)
acc_sgd = train_sgd(model_sgd, tasks)

print("\n" + "="*60)
print("RUNNING CGL (ALPHA-GATED)")
print("="*60)
model_cgl = MLP(hidden_sizes=CONFIG['hidden_sizes']).to(device)
acc_cgl, history, gates, efficiency = train_cgl(model_cgl, tasks)

# ==============================================================================
# COMPUTE METRICS
# ==============================================================================

def compute_cl_metrics(acc_matrix):
    n = acc_matrix.shape[0]
    
    # Average Accuracy (AA)
    final_accs = acc_matrix[-1]
    aa = final_accs.mean()
    
    # Backward Transfer (BWT) / Forgetting
    forgetting = 0
    bwt = 0
    for j in range(n - 1):
        peak_acc = acc_matrix[j:n, j].max()
        final_acc = acc_matrix[-1, j]
        forgetting += peak_acc - final_acc
        bwt += final_acc - acc_matrix[j, j]
        
    avg_forgetting = forgetting / (n - 1)
    avg_bwt = bwt / (n - 1)
    
    return aa, avg_forgetting, avg_bwt

aa_sgd, forg_sgd, bwt_sgd = compute_cl_metrics(acc_sgd)
aa_cgl, forg_cgl, bwt_cgl = compute_cl_metrics(acc_cgl)

print("\n" + "="*60)
print("FINAL RESULTS")
print("="*60)
print(f"{'Method':<20} {'Avg Acc (AA)':<15} {'BWT':<15} {'Efficiency':<15}")
print("-" * 65)
print(f"{'SGD (Baseline)':<20} {aa_sgd*100:.2f}%{'':<5} {bwt_sgd*100:.2f}%{'':<5} 100.0%")
print(f"{'CGL (Alpha-Gated)':<20} {aa_cgl*100:.2f}%{'':<5} {bwt_cgl*100:.2f}%{'':<5} {efficiency*100:.1f}%")
print("-" * 65)
print(f"{'Improvement':<20} {(aa_cgl-aa_sgd)*100:+.2f}%{'':<5} {(bwt_cgl-bwt_sgd)*100:+.2f}%")
print("="*60)
# Result summaries are printed above.

# ==============================================================================
# FIGURE 1: ACCURACY MATRICES
# ==============================================================================

fig, axes = plt.subplots(1, 2, figsize=(12, 5))

for ax, acc, title in zip(axes, [acc_sgd, acc_cgl], 
                           [f'SGD (AA={aa_sgd*100:.1f}%, BWT={bwt_sgd*100:.1f}%)',
                            f'CGL (AA={aa_cgl*100:.1f}%, BWT={bwt_cgl*100:.1f}%)']):
    im = ax.imshow(acc * 100, cmap='RdYlGn', vmin=0, vmax=100)
    ax.set_xlabel('Evaluated on Task')
    ax.set_ylabel('After Training on Task')
    ax.set_title(title)
    ax.set_xticks(range(5))
    ax.set_yticks(range(5))
    
    for i in range(5):
        for j in range(5):
            v = acc[i, j] * 100
            ax.text(j, i, f'{v:.0f}', ha='center', va='center',
                   color='white' if v < 50 else 'black')

plt.colorbar(im, ax=axes, shrink=0.8, label='Accuracy (%)')
plt.tight_layout()
plt.savefig('fig1_accuracy_matrix.png', dpi=150)
# plt.show() # Disabled for headless

# ==============================================================================
# FIGURE 2: ALPHA DRIFT DYNAMICS
# ==============================================================================

fig, axes = plt.subplots(1, 3, figsize=(14, 4))

steps = range(len(history['coherence']))
n_steps = len(steps)
task_boundaries = [n_steps // 5 * i for i in range(1, 5)]

# Coherence
ax = axes[0]
ax.plot(history['coherence'], alpha=0.3, label='Raw')
ax.plot(history['ema'], 'r-', lw=2, label='EMA')
for tb in task_boundaries:
    ax.axvline(tb, color='gray', ls='--', alpha=0.5)
ax.set_xlabel('Step')
ax.set_ylabel('Coherence (max prob)')
ax.set_title('(a) Coherence')
ax.legend()
ax.grid(True, alpha=0.3)

# Alpha
ax = axes[1]
ax.plot(history['alpha_norm'], 'b-', alpha=0.7)
ax.axhline(0, color='black', lw=0.5)
for tb in task_boundaries:
    ax.axvline(tb, color='gray', ls='--', alpha=0.5)
ax.set_xlabel('Step')
ax.set_ylabel('Normalized Alpha (Z-score)')
ax.set_title('(b) Alpha Drift (Normalized)')
ax.grid(True, alpha=0.3)

# Gate
ax = axes[2]
ax.plot(history['gate'], 'g-', alpha=0.7)
for tb in task_boundaries:
    ax.axvline(tb, color='gray', ls='--', alpha=0.5)
ax.set_xlabel('Step')
ax.set_ylabel('Gate')
ax.set_title('(c) Learning Gate')
ax.set_ylim(0, 1.05)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('fig2_alpha_drift.png', dpi=150)
# plt.show() # Disabled for headless

# ==============================================================================
# FIGURE 3: COMPARISON
# ==============================================================================

fig, axes = plt.subplots(1, 2, figsize=(12, 4))

# Task trajectories
ax = axes[0]
colors = plt.cm.tab10(np.linspace(0, 1, 5))
for t in range(3):
    ax.plot(range(5), acc_sgd[:, t]*100, 'o--', color=colors[t], alpha=0.5, label=f'Task {t} (SGD)')
    ax.plot(range(5), acc_cgl[:, t]*100, 's-', color=colors[t], label=f'Task {t} (CGL)')
ax.set_xlabel('After Training on Task')
ax.set_ylabel('Accuracy (%)')
ax.set_title('(a) Task Accuracy Over Training')
ax.legend(loc='lower left', fontsize=8)
ax.grid(True, alpha=0.3)
ax.set_ylim(0, 105)

# Bars
ax = axes[1]
x = np.arange(2)
w = 0.35
bars1 = ax.bar(x - w/2, [aa_sgd*100, aa_cgl*100], w, label='Average Accuracy (AA)', color='steelblue')
bars2 = ax.bar(x + w/2, [bwt_sgd*100, bwt_cgl*100], w, label='BWT (Forgetting)', color='coral')
ax.set_xticks(x)
ax.set_xticklabels(['SGD\n(Baseline)', 'CGL\n(Alpha-Gated)'])
ax.set_ylabel('Percentage (%)')
ax.set_title('(b) CL Metrics Comparison')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

for bar in list(bars1) + list(bars2):
    ax.annotate(f'{bar.get_height():.1f}%', xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
               xytext=(0, 3), textcoords='offset points', ha='center')

plt.tight_layout()
plt.savefig('fig3_comparison.png', dpi=150)
# plt.show() # Disabled for headless

print(f"\n✅ Done! Efficiency: {efficiency*100:.1f}% updates performed.")
