
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import matplotlib.pyplot as plt
import os

# ==============================================================================
# CHIMERA V12 - THE MODE KEEPER (COGNITIVE PERSISTENCE)
# ==============================================================================

CONFIG = {
    'latent_dim': 128,           # Smaller dim is sufficient for this logic
    'spectral_radius': 0.99,      # High radius for long memory (near 1.0)
    'input_scale': 0.1,           # Low input gain to adapt slowly/resist noise
    'leak_rate': 0.1,            # Low leak rate = Long memory time constant
    
    'seq_len_train': 100,
    'seq_len_test': 2000,         # TEST LONG TERM RETENTION
    'batch_size': 32,
    'steps': 2000,                # Training steps
    
    'switch_prob': 0.02,          # Sparse switches in training
    'seed': 42
}

torch.manual_seed(CONFIG['seed'])
np.random.seed(CONFIG['seed'])
device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
print(f"Device: {device}")

# ==============================================================================
# DATASET: THE MODE KEEPER
# ==============================================================================

def generate_batch(batch_size, seq_len, switch_prob=0.02):
    # Inputs: [batch, seq, 2] -> Feature 0: x (random), Feature 1: c (command)
    x = torch.rand(batch_size, seq_len) * 2 - 1 # [-1, 1]
    
    # Generate commands
    # 0 = Maintain, 1 = Sine, -1 = Cosine
    # Sample sparse switches
    c = torch.zeros(batch_size, seq_len)
    
    # State tracking for targets
    # Start random state: 0 (Sine) or 1 (Cosine)
    current_state = torch.randint(0, 2, (batch_size,)).float() # 0 or 1
    
    targets = torch.zeros(batch_size, seq_len)
    
    for t in range(seq_len):
        # Roll for switch
        switches = torch.rand(batch_size) < switch_prob
        
        # Apply switches
        # If switch and rand < 0.5 -> State 0
        # If switch and rand > 0.5 -> State 1
        new_mode_vals = torch.rand(batch_size)
        
        # Update command signal
        # c=1 means "Switch to Sine (State 0)" -> actually let's map:
        # State 0 (Sine) -> c = 1
        # State 1 (Cos)  -> c = -1
        # No Switch      -> c = 0
        
        for b in range(batch_size):
            if switches[b]:
                target_mode = 0 if new_mode_vals[b] < 0.5 else 1
                if target_mode == 0:
                    c[b, t] = 1.0 # Command: Be Sine
                    current_state[b] = 0.0
                else:
                    c[b, t] = -1.0 # Command: Be Cosine
                    current_state[b] = 1.0
            
            # Compute Target based on current state (instantaneous)
            if current_state[b] == 0.0:
                targets[b, t] = torch.sin(np.pi * x[b, t])
            else:
                targets[b, t] = torch.cos(np.pi * x[b, t])
                
    inputs = torch.stack([x, c], dim=2) # [batch, seq, 2]
    return inputs.to(device), targets.unsqueeze(2).to(device)

# ==============================================================================
# MODELS
# ==============================================================================

class LSTMBaseline(nn.Module):
    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(input_size=2, hidden_size=CONFIG['latent_dim'], batch_first=True)
        self.head = nn.Linear(CONFIG['latent_dim'], 1)
        
    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out)

class ChimeraCognitive(nn.Module):
    def __init__(self):
        super().__init__()
        self.latent_dim = CONFIG['latent_dim']
        
        # Fixed Reservoir (Leaky Integrator ESN)
        W_res = torch.randn(self.latent_dim, self.latent_dim)
        u, s, v = torch.svd(W_res)
        s[0] = CONFIG['spectral_radius']
        W_res = torch.mm(torch.mm(u, torch.diag(s)), v.t())
        self.W_res = nn.Parameter(W_res, requires_grad=False)
        
        # Input weights (2 inputs)
        self.W_in = nn.Parameter(torch.randn(2, self.latent_dim) * CONFIG['input_scale'], requires_grad=False)
        self.bias = nn.Parameter(torch.randn(self.latent_dim) * 0.1, requires_grad=False)
        
        self.leak = CONFIG['leak_rate']
        self.tanh = nn.Tanh()
        
        self.readout = nn.Linear(self.latent_dim, 1)
        
    def forward(self, x):
        # x: [batch, seq, 2]
        batch, seq, _ = x.size()
        h = torch.zeros(batch, self.latent_dim).to(x.device)
        
        outputs = []
        
        # Explicit unroll for manual leaky update
        for t in range(seq):
            u_t = x[:, t, :] # [batch, 2]
            
            # Update: h_new = (1-a)*h + a * tanh(W*h + Win*u)
            drive = torch.mm(h, self.W_res) + torch.mm(u_t, self.W_in) + self.bias
            h_new = self.tanh(drive)
            h = (1 - self.leak) * h + self.leak * h_new
            
            outputs.append(h)
            
        outputs = torch.stack(outputs, dim=1) # [batch, seq, dim]
        return self.readout(outputs)

# ==============================================================================
# EXPERIMENT LOOP
# ==============================================================================

def train_and_test(model_cls, name):
    print(f"\n>>> Testing {name} <<<")
    model = model_cls().to(device)
    optimizer = optim.Adam(model.parameters(), lr=0.005)
    criterion = nn.MSELoss()
    
    # 1. TRAIN (Short sequences, frequent switching)
    print("Training on short, switching sequences...")
    for step in range(CONFIG['steps']):
        x, y = generate_batch(CONFIG['batch_size'], CONFIG['seq_len_train'], switch_prob=0.05)
        
        optimizer.zero_grad()
        pred = model(x)
        loss = criterion(pred, y)
        loss.backward()
        optimizer.step()
        
        if step % 500 == 0:
            print(f"  Step {step}: MSE = {loss.item():.5f}")
            
    # 2. TEST LONG RETENTION (Force Switch ONCE, then SILENCE)
    print("Testing Long-Term Retention (2000 steps)...")
    
    # Custom Test Batch: 
    # T=0: Command = Sine (1.0)
    # T=1..1999: Command = 0.0 (Silence)
    # Target: Sine wave throughout
    
    x_test = torch.rand(1, CONFIG['seq_len_test']) * 2 - 1
    c_test = torch.zeros(1, CONFIG['seq_len_test'])
    c_test[0, 0] = 1.0 # TRIGGER ONLY AT START
    
    inputs = torch.stack([x_test, c_test], dim=2).to(device) # [1, 2000, 2]
    targets = torch.sin(np.pi * x_test).unsqueeze(2).to(device)
    
    with torch.no_grad():
        preds = model(inputs)
    
    # Plotting first 50 vs last 50
    preds = preds.cpu().squeeze().numpy()
    targets = targets.cpu().squeeze().numpy()
    
    # Calculate error in last 100 steps
    final_mse = ((preds[-100:] - targets[-100:])**2).mean()
    print(f"  Final 100-step MSE: {final_mse:.5f}")
    
    # Generate Plot
    plt.figure(figsize=(10, 4))
    plt.subplot(1, 2, 1)
    plt.title(f"{name}: First 100 Steps (Triggered)")
    plt.plot(targets[:100], label='Target (Sine)', alpha=0.6)
    plt.plot(preds[:100], label='Pred', linestyle='--')
    plt.legend()
    
    plt.subplot(1, 2, 2)
    plt.title(f"{name}: Last 100 Steps (T=1900-2000)")
    plt.plot(range(1900, 2000), targets[-100:], label='Target', alpha=0.6)
    plt.plot(range(1900, 2000), preds[-100:], label='Pred', linestyle='--')
    plt.ylim(-1.5, 1.5)
    
    filename = f"chimera_v12_{name.lower().replace(' ', '_')}.png"
    plt.tight_layout()
    plt.savefig(filename)
    print(f"  Saved plot to {filename}")
    
    return final_mse

if __name__ == "__main__":
    print("="*60)
    print("THE MODE KEEPER CHALLENGE")
    print("="*60)
    
    err_lstm = train_and_test(LSTMBaseline, "LSTM Baseline")
    err_chimera = train_and_test(ChimeraCognitive, "Chimera Cognitive")
    
    print("\n" + "="*60)
    print("FINAL RESULTS: RETENTION MSE (Lower is Better)")
    print("="*60)
    print(f"LSTM Baseline:     {err_lstm:.5f}")
    print(f"Chimera Cognitive: {err_chimera:.5f}")
    
    if err_chimera < err_lstm:
        print("\n>>> RESULT: CHIMERA WINS. Reservoir holds state longer.")
    else:
        print("\n>>> RESULT: LSTM WINS.")
    print("="*60)
