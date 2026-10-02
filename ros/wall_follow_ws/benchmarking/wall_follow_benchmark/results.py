"""Completion checks shared by execution and offline result discovery."""
import json


def completed(path):
    try:
        metadata = json.loads((path/'metadata.json').read_text())
        return (metadata.get('schema_version') == 3 and metadata.get('completed') is True
                and (path/'poses.csv').is_file()
                and (path/'samples.csv').is_file()
                and len((path/'samples.csv').read_text().splitlines()) >= 3)
    except (OSError, ValueError):
        return False


def eligible(path):
    if not completed(path):
        return False
    if not (path/'attempt.json').exists():
        return True  # Direct runtime runs have no suite attempt record.
    try:
        return json.loads((path/'attempt.json').read_text()).get('status') == 'completed'
    except (OSError, ValueError):
        return False
