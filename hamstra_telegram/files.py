"""File operations shared by the downloader and the viewer."""
import os
import time

REPLACE_BACKOFF = (0.1, 0.2, 0.5, 1, 2)


def replace(source: str, destination: str, sleep=time.sleep) -> None:
    """os.replace, retried a few times: on Windows the rename fails while another process holds the destination open."""
    for delay in REPLACE_BACKOFF:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            sleep(delay)
    os.replace(source, destination)
