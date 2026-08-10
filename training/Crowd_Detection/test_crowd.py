import cv2
import os
from lwcc import LWCC

# ---------------------------------
# Configuration
# ---------------------------------
VIDEO_PATH = r"test/crowd.mp4"
OUTPUT_PATH = r"outputs/crowd_result.mp4"

# Crowd alert threshold
CROWD_THRESHOLD = 40

# Process every 5th frame to reduce CPU usage
FRAME_SKIP = 5

# ---------------------------------
# Create output folder
# ---------------------------------
os.makedirs("outputs", exist_ok=True)

# ---------------------------------
# Open video
# ---------------------------------
cap = cv2.VideoCapture(VIDEO_PATH)

if not cap.isOpened():
    print("ERROR: Could not open video.")
    exit()

width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)

writer = cv2.VideoWriter(
    OUTPUT_PATH,
    cv2.VideoWriter_fourcc(*"mp4v"),
    fps,
    (width, height)
)

frame_number = 0
processed_frames = 0
max_count = 0
crowd_alert_detected = False

print("Starting crowd detection...")

# ---------------------------------
# Process video
# ---------------------------------
while True:

    ret, frame = cap.read()

    if not ret:
        break

    frame_number += 1

    # Process only every 5th frame
    if frame_number % FRAME_SKIP != 0:
        writer.write(frame)
        continue

    processed_frames += 1

    # Temporary frame for LWCC
    temp_frame = "temp_crowd_frame.jpg"
    cv2.imwrite(temp_frame, frame)

    try:

        # ---------------------------------
        # Crowd Counting
        # ---------------------------------
        count, density = LWCC.get_count(
            temp_frame,
            model_name="DM-Count",
            model_weights="SHA",
            return_density=True
        )

        count = int(round(count))

        max_count = max(max_count, count)

        # ---------------------------------
        # Crowd Alert
        # ---------------------------------
        if count >= CROWD_THRESHOLD:
            crowd_alert_detected = True

    except Exception as e:

        print(f"Error processing frame {frame_number}: {e}")

    # Write original frame without overlay
    writer.write(frame)

# ---------------------------------
# Cleanup
# ---------------------------------
cap.release()
writer.release()

if os.path.exists("temp_crowd_frame.jpg"):
    os.remove("temp_crowd_frame.jpg")

# ---------------------------------
# Final Result
# ---------------------------------
print("--------------------------------")
print("Crowd detection completed.")
print(f"Frames processed: {processed_frames}")
print(f"Maximum crowd count: {max_count}")
print(f"Crowd threshold: {CROWD_THRESHOLD}")

if crowd_alert_detected:
    print("Crowd Alert: HIGH")
else:
    print("Crowd Alert: NORMAL")

print(f"Output saved to: {OUTPUT_PATH}")
print("--------------------------------")