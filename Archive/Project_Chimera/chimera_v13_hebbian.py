
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import matplotlib.pyplot as plt
import os

# ==============================================================================
# CHIMERA V13 - HEBBIAN PLASTICITY (OJA'S RULE)
# ==============================================================================

CONFIG = {
    'latent_dim': 128,
    'spectral_radius': 0.95,      # Start slightly chaotic
    'input_scale': 0.5,
    'leak_rate': 0.2,             
    
    'hebbian_rate': 0.001,        # Plasticity Rate (eta)
    'hebbian_steps': 5000,        # How long to imprint
    
    'seq_len_test': 2000,
    'seed': 42
}

torch.manual_seed(CONFIG['seed'])
np.random.seed(CONFIG['seed'])
device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
print(f"Device: {device}")

# ==============================================================================
# HEBBIAN RESERVOIR
# ==============================================================================

class HebbianChimera(nn.Module):
    def __init__(self):
        super().__init__()
        self.latent_dim = CONFIG['latent_dim']
        
        # Reservoir Weights (Plastic)
        W_res = torch.randn(self.latent_dim, self.latent_dim)
        u, s, v = torch.svd(W_res)
        s[0] = CONFIG['spectral_radius']
        W_res = torch.mm(torch.mm(u, torch.diag(s)), v.t())
        self.W_res = nn.Parameter(W_res, requires_grad=False) # No SGD, only Hebb
        
        # Input Weights (Fixed)
        self.W_in = nn.Parameter(torch.randn(2, self.latent_dim) * CONFIG['input_scale'], requires_grad=False)
        self.bias = nn.Parameter(torch.randn(self.latent_dim) * 0.1, requires_grad=False)
        
        self.leak = CONFIG['leak_rate']
        self.tanh = nn.Tanh()
        self.readout = nn.Linear(self.latent_dim, 1) # Learned by SGD
        
    def forward_plastic(self, x, plasticity=False):
        # x: [batch, seq, 2]
        batch, seq, _ = x.size()
        h = torch.zeros(batch, self.latent_dim).to(x.device)
        
        outputs = []
        
        for t in range(seq):
            u_t = x[:, t, :]
            
            # 1. Update State
            # drive = h @ W + u @ Win + b
            drive = torch.mm(h, self.W_res) + torch.mm(u_t, self.W_in) + self.bias
            h_new = self.tanh(drive)
            h_next = (1 - self.leak) * h + self.leak * h_new
            
            # 2. Hebbian Update (Oja's Rule)
            # dW = eta * (h_next.T @ h - W * h_next^2) ... simplified:
            # Oja: dW = eta * y * (x - y*W) 
            # Recurrent Oja: dW_ij = eta * h_i(t) * (h_j(t-1) - h_i(t) * W_ij)
            if plasticity:
                with torch.no_grad():
                    # h_prev = h
                    # h_curr = h_next
                    # dW = eta * h_curr.T @ h_prev - eta * h_curr^2 * W
                    
                    # Vectorized batch Oja
                    # Batched outer product: [B, N, 1] @ [B, 1, N] -> [B, N, N]
                    hebb_term = torch.bmm(h_next.unsqueeze(2), h.unsqueeze(1)).mean(dim=0)
                    decay_term = torch.mm(torch.diag(torch.mean(h_next ** 2, dim=0)), self.W_res)
                    
                    dW = hebb_term - decay_term
                    self.W_res += CONFIG['hebbian_rate'] * dW
                    
            h = h_next
            outputs.append(h)
            
        return torch.stack(outputs, dim=1)

# ==============================================================================
# 2-PHASE EXPERIMENT
# ==============================================================================

def run_experiment():
    print(">>> CHIMERA V13: HEBBIAN IMPRINTING <<<")
    
    model = HebbianChimera().to(device)
    
    # ----------------------------------------------------
    # PHASE 1: IMPRINTING (Unsupervised Plasticity)
    # ----------------------------------------------------
    print(f"Imprinting for {CONFIG['hebbian_steps']} steps...")
    
    # Generate Pure Sine/Cosine waves to imprint dynamics
    t = torch.linspace(0, 100, 1000)
    sine_wave = torch.sin(t)
    cos_wave = torch.cos(t)
    
    # Prepare batch: Half Sine, Half Cosine
    # Input Feature 0: Value
    # Input Feature 1: Command (1 for Sin, -1 for Cos)
    
    batch_size = 32
    seq_len = 100
    
    for step in range(CONFIG['hebbian_steps'] // seq_len):
        # Generate random Sine/Cos mode data
        inputs = []
        for b in range(batch_size):
            mode = np.random.randint(0, 2)
            noise = torch.randn(seq_len) * 0.1
            if mode == 0:
                vals = torch.sin(torch.linspace(0, 4*np.pi, seq_len)) + noise
                cmd = torch.ones(seq_len)
            else:
                vals = torch.cos(torch.linspace(0, 4*np.pi, seq_len)) + noise
                cmd = torch.ones(seq_len) * -1
            inputs.append(torch.stack([vals, cmd], dim=1))
            
        x = torch.stack(inputs).to(device)
        
        # Run with Plasticity ON
        model.forward_plastic(x, plasticity=True)
        
        if step % 10 == 0:
            norm = model.W_res.norm().item()
            print(f"  Step {step*seq_len}: W_res Norm = {norm:.4f}")

    print("Imprinting Complete. W_res is now Fixed.")
    
    # ----------------------------------------------------
    # PHASE 2: READOUT TRAINING (Supervised, Fixed W_res)
    # ----------------------------------------------------
    print("\nTraining Readout (Linear Regression)...")
    optimizer = optim.Adam(model.readout.parameters(), lr=0.01)
    criterion = nn.MSELoss()
    
    for step in range(1000):
        # Generate data
        x = torch.rand(32, 50) * 2 - 1
        c = torch.randint(0, 2, (32, 50)).float() * 2 - 1 # -1 or 1
        targets = []
        for b in range(32):
            if c[b,0] == 1: targets.append(torch.sin(np.pi * x[b]))
            else: targets.append(torch.cos(np.pi * x[b]))
        targets = torch.stack(targets).to(device).unsqueeze(2)
        
        inputs = torch.stack([x, c], dim=2).to(device)
        
        optimizer.zero_grad()
        # Plasticity OFF
        states = model.forward_plastic(inputs, plasticity=False)
        preds = model.readout(states)
        loss = criterion(preds, targets)
        loss.backward()
        optimizer.step()
        
        if step % 200 == 0:
            print(f"  Readout Step {step}: MSE = {loss.item():.5f}")

    # ----------------------------------------------------
    # PHASE 3: THE MODE KEEPER TEST (Retention)
    # ----------------------------------------------------
    print("\nTesting Retention (Trigger once, hold for 2000)...")
    
    # Test Sequence: Trigger SINE at t=0, then silence
    x_test = torch.rand(1, 2000) * 2 - 1
    c_test = torch.zeros(1, 2000)
    c_test[0, 0] = 1.0 # Trigger Sine
    
    inputs = torch.stack([x_test, c_test], dim=2).to(device)
    targets = torch.sin(np.pi * x_test).unsqueeze(2).to(device)
    
    with torch.no_grad():
        preds = model.readout(model.forward_plastic(inputs, plasticity=False))
        
    preds = preds.cpu().squeeze().numpy()
    targets = targets.cpu().squeeze().numpy()
    
    mse_final = ((preds[-100:] - targets[-100:])**2).mean()
    print(f"Final 100-step MSE: {mse_final:.5f}")
    
    # Plot
    plt.figure(figsize=(10, 4))
    plt.subplot(1, 2, 1)
    plt.title("Hebbian Chimera: First 100")
    plt.plot(targets[:100], label='Target')
    plt.plot(preds[:100], label='Pred')
    
    plt.subplot(1, 2, 2)
    plt.title("Hebbian Chimera: Last 100 (T=1900-2000)")
    plt.plot(range(1900, 2000), targets[-100:], label='Target')
    plt.plot(range(1900, 2000), preds[-100:], label='Pred')
    plt.savefig('chimera_v13_hebbian.png')
    print("Saved plot.")

if __name__ == "__main__":
    run_experiment()
