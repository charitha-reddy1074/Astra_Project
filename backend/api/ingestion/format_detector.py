import os


def detect_format(filename):

    extension = os.path.splitext(
        filename
    )[1].lower()

    return extension