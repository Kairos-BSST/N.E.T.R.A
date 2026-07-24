import torch
from torchvision.models.video import mc3_18

# Create model
model = mc3_18(weights=None)

# Replace classifier for binary classification
model.fc = torch.nn.Linear(model.fc.in_features, 2)

# Load weights
state = torch.load("models/best.pth", map_location="cpu")

model.load_state_dict(state)

model.eval()

print("✅ Model loaded successfully!")

# Test with dummy video
dummy = torch.randn(1, 3, 16, 112, 112)

with torch.no_grad():
    output = model(dummy)

print("Output shape:", output.shape)
print(output)
