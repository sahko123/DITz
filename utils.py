
import datetime
from pathlib import Path

def get_base_tokens(p: Path = Path(), index: int = 1, file_type: str = "unknown") -> dict:
    """Generate standard tokens from a file path."""
    stat = p.stat()
    dt = datetime.datetime.fromtimestamp(stat.st_mtime)
    return {
        "type": file_type,
        "file_date": stat.st_mtime,
        "file_year": dt.year,
        "file_month": dt.month,
        "file_day": dt.day,
        "stem": p.stem,
        "ext": p.suffix,
        "parent": p.parent.name,
        "index": index
    }

def get_base_token_keys():
    return get_base_tokens().keys()

def clean_unmatched_braces(template: str) -> str:
        """
        Cleans a template string by removing unmatched braces
        to prevent string.Formatter from throwing errors.
        """
        result = []
        brace_stack = 0

        for i, char in enumerate(template):
            if char == '{':
                brace_stack += 1
                result.append(char)
            elif char == '}':
                if brace_stack > 0:
                    brace_stack -= 1
                    result.append(char)
                # else: skip unmatched closing brace
            else:
                result.append(char)

        # Remove unmatched opening braces from end
        cleaned = ''.join(result)
        while cleaned.count('{') > cleaned.count('}'):
            cleaned = cleaned.rsplit('{', 1)[0] + cleaned.rsplit('{', 1)[-1].replace('{', '')
        return cleaned