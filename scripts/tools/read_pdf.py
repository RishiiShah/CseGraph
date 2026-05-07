import sys

try:
    import pypdf
except ImportError as exc:
    print("Missing dependency 'pypdf'. Install dependencies with: env/bin/pip install -r requirements.txt")
    raise SystemExit(1) from exc

def extract_text(pdf_path):
    try:
        reader = pypdf.PdfReader(pdf_path)
        text = ""
        for i, page in enumerate(reader.pages):
            text += f"--- Page {i+1} ---\n"
            text += page.extract_text() + "\n"
        print(text)
    except Exception as e:
        print(f"Error reading PDF: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        extract_text(sys.argv[1])
    else:
        print("Please provide a PDF path.")
