"""
main.py
-------
Entry point for the N.E.T.R.A cloud video-fetch service.

Run this as a standalone background process (or inside a Docker container /
systemd service / Kubernetes pod). It will continuously poll your GCS
bucket and download new video files as they arrive, then hand each one to
`handle_new_video`, which is where you plug in the rest of the pipeline:
AI analytics, event detection, alerting, dashboard ingest, etc.

Usage:
    python main.py            # run continuously
    python main.py --once     # run a single fetch cycle and exit (good for cron / testing)
"""

import argparse
import logging

from cloud_integration.video_fetcher import VideoFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def handle_new_video(local_path: str, blob_name: str) -> None:
    """
    Called once per newly downloaded video. Replace the body of this
    function with real integration into the rest of N.E.T.R.A, e.g.:

        - push (local_path, blob_name) onto a task queue (Celery/RabbitMQ/Redis)
          for your AI analytics workers to pick up
        - kick off a subprocess / model inference job directly
        - insert a row into your "videos" table so the dashboard shows it
    """
    logging.info("New video ready for processing: %s (source: %s)", local_path, blob_name)
    # TODO: hand off to analytics module, e.g.:
    # analytics_queue.enqueue(local_path=local_path, source=blob_name)


def main():
    parser = argparse.ArgumentParser(description="N.E.T.R.A cloud video fetcher")
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single fetch cycle and exit, instead of polling forever."
    )
    args = parser.parse_args()

    fetcher = VideoFetcher(on_new_video=handle_new_video)

    if args.once:
        count = fetcher.fetch_once()
        logging.info("Done. %d new video(s) fetched.", count)
    else:
        fetcher.run_forever()


if __name__ == "__main__":
    main()