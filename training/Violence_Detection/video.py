import cv2
import torch
import torchvision
import numpy as np
import albumentations as A
from collections import deque

# -----------------------------
# Configuration
# -----------------------------
VIDEO_PATH = "test/fight.mp4"
MODEL_PATH = "models/best.pth"
OUTPUT_PATH = "outputs/output.mp4"

SEQUENCE_LENGTH = 16
SKIP = 2

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Using:", DEVICE)

# -----------------------------
# Transform (Exactly like author)
# -----------------------------
transform = A.Compose([
    A.Resize(128, 171),
    A.CenterCrop(112, 112),
    A.Normalize(
        mean=[0.43216, 0.394666, 0.37645],
        std=[0.22803, 0.22145, 0.216989]
    )
])

# -----------------------------
# Load Model
# -----------------------------
model = torchvision.models.video.mc3_18(weights=None)

num_ftrs = model.fc.in_features
model.fc = torch.nn.Linear(num_ftrs, 2)

model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))

model.to(DEVICE)
model.eval()

print("Model Loaded Successfully")

# -----------------------------
# Video
# -----------------------------
cap = cv2.VideoCapture(VIDEO_PATH)

if not cap.isOpened():
    print("Cannot open video.")
    exit()

width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)

writer = cv2.VideoWriter(
    OUTPUT_PATH,
    cv2.VideoWriter_fourcc(*'mp4v'),
    fps,
    (width, height)
)

frames = deque(maxlen=SEQUENCE_LENGTH)

frame_counter = 0

prediction = "Collecting..."
confidence = 0

while True:

    ret, frame = cap.read()

    if not ret:
        break

    display = frame.copy()

    # Sample every 2nd frame
    if frame_counter % SKIP == 0:

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        processed = transform(image=rgb)["image"]

        frames.append(processed)

    frame_counter += 1

    if len(frames) == SEQUENCE_LENGTH:

        clip = np.array(frames)

        # (16,H,W,C) -> (1,C,16,H,W)
        clip = np.transpose(clip, (3,0,1,2))
        clip = np.expand_dims(clip, axis=0)

        clip = torch.tensor(
            clip,
            dtype=torch.float32
        ).to(DEVICE)

        with torch.no_grad():

            output = model(clip)

            prob = torch.softmax(output, dim=1)

            conf, pred = torch.max(prob, 1)

            confidence = conf.item()*100

            pred = pred.item()

        # Repository labels:
        # 0 = fight
        # 1 = noFight

        if pred == 0:
            prediction = "FIGHT"
            color = (0,0,255)
        else:
            prediction = "NO FIGHT"
            color = (0,255,0)

        # ---------- IMPORTANT ----------
        # Keep last 8 frames
        # Better than clearing queue
        # --------------------------------

        temp = list(frames)[8:]

        frames.clear()

        frames.extend(temp)

    cv2.putText(
        display,
        f"{prediction} {confidence:.1f}%",
        (20,40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        color if prediction!="Collecting..." else (255,255,0),
        2
    )

    writer.write(display)

    cv2.imshow("Violence Detection", display)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
writer.release()

cv2.destroyAllWindows()

print("Done!")
print("Saved to:", OUTPUT_PATH)
