import cv2
import torch
import torch.nn as nn
import numpy as np

# ============================================================
# Model
# ============================================================

class ConvolutionalAutoencoder(nn.Module):

    def __init__(self, input_channels=1, latent_dim=256):
        super().__init__()

        self.encoder = nn.Sequential(
            nn.Conv2d(input_channels,32,4,2,1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(32),

            nn.Conv2d(32,64,4,2,1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(64),

            nn.Conv2d(64,128,4,2,1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(128),

            nn.Conv2d(128,256,4,2,1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(256),
        )

        self.flatten = nn.Flatten()

        self.encode_fc = nn.Linear(4*4*256,256)

        self.decode_fc = nn.Linear(256,4*4*256)

        self.decoder = nn.Sequential(

            nn.ConvTranspose2d(256,128,4,2,1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(128),

            nn.ConvTranspose2d(128,64,4,2,1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(64),

            nn.ConvTranspose2d(64,32,4,2,1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(32),

            nn.ConvTranspose2d(32,input_channels,4,2,1),

            nn.Sigmoid()
        )

    def forward(self,x):

        x=self.encoder(x)

        b=x.size(0)

        x=self.flatten(x)

        x=self.encode_fc(x)

        x=self.decode_fc(x)

        x=x.view(b,256,4,4)

        x=self.decoder(x)

        return x


# ============================================================
# Paths
# ============================================================

MODEL_PATH="models/anomaly.pth"
VIDEO_PATH="test/fight.mp4"
OUTPUT_PATH="outputs/output.mp4"

device=torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ============================================================
# Load checkpoint
# ============================================================

checkpoint=torch.load(MODEL_PATH,map_location=device)

model=ConvolutionalAutoencoder(
    checkpoint["model_info"]["input_channels"],
    checkpoint["model_info"]["latent_dimension"]
).to(device)

model.load_state_dict(checkpoint["model_state_dict"])

model.eval()

print("Model Loaded")

# ============================================================
# Video
# ============================================================

cap=cv2.VideoCapture(VIDEO_PATH)

if not cap.isOpened():
    print("Cannot open video")
    exit()

width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps=cap.get(cv2.CAP_PROP_FPS)

writer=cv2.VideoWriter(
    OUTPUT_PATH,
    cv2.VideoWriter_fourcc(*'mp4v'),
    fps,
    (width,height)
)

errors=[]

THRESHOLD=0.01

while True:

    ret,frame=cap.read()

    if not ret:
        break

    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)

    gray=cv2.resize(gray,(64,64))

    gray=gray.astype(np.float32)/255.0

    tensor=torch.from_numpy(gray)

    tensor=tensor.unsqueeze(0).unsqueeze(0).to(device)

    with torch.no_grad():

        reconstruction=model(tensor)

        error=torch.mean((tensor-reconstruction)**2).item()

    errors.append(error)

    if error>THRESHOLD:

        label="ANOMALY"

        color=(0,0,255)

    else:

        label="NORMAL"

        color=(0,255,0)

    cv2.putText(
        frame,
        f"{label}  Error:{error:.5f}",
        (20,40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        color,
        2
    )

    writer.write(frame)

    cv2.imshow("Anomaly Detection",frame)

    if cv2.waitKey(1)&0xFF==27:
        break

cap.release()

writer.release()

cv2.destroyAllWindows()

print("Average Error :",np.mean(errors))

print("Maximum Error :",np.max(errors))
