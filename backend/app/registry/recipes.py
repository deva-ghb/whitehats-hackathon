"""How each fixed build is made: the upstream release, the backported fix, and the proof that it holds.

A recipe names the exact upstream source (its PyPI sha256), the fix as a patch with where it came from, and a proof:
a script run against the original and against the fixed build, which must fail on the first and pass on the second.
"""

from __future__ import annotations

from dataclasses import dataclass

FLAW_SEEN = 3  # the exit code a proof gives when it has seen the flaw itself, not some other failure


@dataclass(frozen=True)
class Recipe:
    key: str
    name: str  # the upstream project, as PyPI names it
    upstream_version: str
    upstream_sha256: str  # of the source release on PyPI; the build stops if the download differs
    package_dirs: tuple[str, ...]  # the import packages the wheel holds (pure Python only)
    version_file: str  # where the package states its own version
    advisories: tuple[str, ...]
    summary: str
    fix_source: str
    patch: str
    proof: str  # a Python script: exit 0 when the flaw is gone, FLAW_SEEN when it is seen; anything else is a broken proof


# A one-page statement whose content stream ends in a comment with no line break. Text extraction on PyPDF2 3.0.1
# never returns on it; the fixed build returns the statement's text at once. A page needs a font, or PyPDF2 never
# parses its content.
_STATEMENT_PROOF = r'''
import signal, sys

content = b"BT /F1 12 Tf 72 712 Td (Statement for October) Tj ET %end"
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
pdf = bytearray(b"%PDF-1.4\n")
offsets = []
for number, body in enumerate(objects, start=1):
    offsets.append(len(pdf))
    pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
xref = len(pdf)
pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)

import io
import PyPDF2


def gave_up(*_):
    print(f"PyPDF2 {PyPDF2.__version__}: reading the crafted statement did not end within 5 seconds")
    sys.exit(3)


signal.signal(signal.SIGALRM, gave_up)
signal.alarm(5)
text = PyPDF2.PdfReader(io.BytesIO(bytes(pdf))).pages[0].extract_text()
signal.alarm(0)
print(f"PyPDF2 {PyPDF2.__version__}: read the crafted statement at once: {text!r}")
sys.exit(0 if text == "Statement for October" else 1)
'''

PYPDF2_3_0_1 = Recipe(
    key="pypdf2-3.0.1-cve-2023-36464",
    name="PyPDF2",
    upstream_version="3.0.1",
    upstream_sha256="a74408f69ba6271f71b9352ef4ed03dc53a31aa404d29b5d31f53bfecfee1440",
    package_dirs=("PyPDF2",),
    version_file="PyPDF2/_version.py",
    advisories=("GHSA-4vvm-4w3v-6mr8", "CVE-2023-36464"),
    summary=("A PDF whose content ends in a comment with no line break sends text extraction into an endless loop "
             "that holds a processor core at 100 percent. PyPDF2 3.0.1 is the last release and has no fix; the fix "
             "is only in the renamed pypdf 3.9.0."),
    fix_source="https://github.com/py-pdf/pypdf/commit/b0e5c689df689ab173df84dacd77b6fc3c161932",
    patch="""--- a/PyPDF2/generic/_data_structures.py
+++ b/PyPDF2/generic/_data_structures.py
@@ -937,7 +937,7 @@
                 # encountering a comment -- but read_object assumes that
                 # following the comment must be the object we're trying to
                 # read.  In this case, it could be an operator instead.
-                while peek not in (b"\\r", b"\\n"):
+                while peek not in (b"\\r", b"\\n", b""):
                     peek = stream.read(1)
             else:
                 operands.append(read_object(stream, None, self.forced_encoding))
""",
    proof=_STATEMENT_PROOF,
)

RECIPES = {recipe.key: recipe for recipe in (PYPDF2_3_0_1,)}
