from ultralytics import YOLO
import cv2
import os

# -----------------------------------
# Load Weapon Detection Model
# -----------------------------------
print("Loading Weapon Detection Model...")

model = YOLO("models/best.pt")

print("Model Loaded Successfully!")

# -----------------------------------
# Video Paths
# -----------------------------------
video_path = "test/weapon_video.mp4"

cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print("Error: Could not open video!")
    exit()

# -----------------------------------
# Video Properties
# -----------------------------------
frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)

os.makedirs("outputs", exist_ok=True)

output_path = "outputs/output.mp4"

fourcc = cv2.VideoWriter_fourcc(*'mp4v')

out = cv2.VideoWriter(
    output_path,
    fourcc,
    fps,
    (frame_width, frame_height)
)

print("Processing Video...\n")

# -----------------------------------
# Process Frames
# -----------------------------------
while True:

    ret, frame = cap.read()

    if not ret:
        break

    results = model(frame)

    for result in results:

        for box in result.boxes:

            x1, y1, x2, y2 = map(int, box.xyxy[0])

            confidence = float(box.conf[0])

            # Ignore weak detections
            if confidence < 0.50:
                continue

            label = f"Weapon {confidence:.2f}"

            # Red Bounding Box
            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (0, 0, 255),
                2
            )

            # Label
            cv2.putText(
                frame,
                label,
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2
            )

    out.write(frame)

    cv2.imshow("Weapon Detection", frame)

    # Press Q to quit
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# -----------------------------------
# Cleanup
# -----------------------------------
cap.release()
out.release()
cv2.destroyAllWindows()

print("\nVideo Processing Complete!")
print(f"Saved to: {output_path}")
