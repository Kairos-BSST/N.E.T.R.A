import torch

ckpt = torch.load("models/anomaly.pth", map_location="cpu")

state = ckpt["model_state_dict"]

for k, v in state.items():
    print(f"{k:40} {tuple(v.shape)}")
