"""
AST Pre-Validation and Static Analysis for Backend Dev Agent.

Performs deterministic syntax and structural validation of Python source code artifacts
before state progression or QA submission, generating structured feedback for agent self-correction.
Adheres strictly to STD-AUD-1: separates parser observations from agent claims.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SyntaxDiagnostic:
    """Detailed observation of a syntax or indentation fault."""
    file_path: str
    error_type: str  # e.g., 'IndentationError', 'SyntaxError', 'TabError'
    line_number: Optional[int]
    column_offset: Optional[int]
    offending_line: Optional[str]
    message: str
    suggestion: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_llm_feedback(self) -> str:
        """Format diagnostic for LLM prompt ingestion during self-correction."""
        lines = [
            f"- File: {self.file_path}",
            f"  Error Type: {self.error_type}",
            f"  Line: {self.line_number or 'Unknown'}, Column: {self.column_offset or 'Unknown'}",
            f"  Problem: {self.message}",
        ]
        if self.offending_line:
            lines.append(f"  Code: `{self.offending_line.strip()}`")
        if self.suggestion:
            lines.append(f"  Fix Guidance: {self.suggestion}")
        return "\n".join(lines)


@dataclass
class ASTValidationResult:
    """Validation report containing deterministic observations."""
    is_valid: bool
    diagnostics: List[SyntaxDiagnostic] = field(default_factory=list)
    parsed_modules: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "error_count": len(self.diagnostics),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "parsed_modules": self.parsed_modules,
        }

    def format_correction_prompt(self) -> str:
        """Construct structured correction guidance for the agent."""
        if self.is_valid:
            return ""
        items = [d.to_llm_feedback() for d in self.diagnostics]
        return (
            "Static AST Pre-Validation detected the following syntax/indentation errors in the generated code:\n\n"
            + "\n\n".join(items)
            + "\n\nPlease repair the syntax and indentation while preserving all intended logic and functionality."
        )


class ASTPreValidator:
    """Static analysis validator utilizing the Python AST compiler."""

    @staticmethod
    def _extract_suggestion(error_type: str, message: str, line_text: Optional[str]) -> str:
        msg_lower = message.lower()
        if error_type == "IndentationError":
            if "unindent does not match any outer indentation level" in msg_lower:
                return "Align this line's indentation level with its enclosing block or statement."
            if "expected an indented block" in msg_lower:
                return "Add 4 spaces of indentation following compound statements (def, if, for, class, try, etc.)."
            return "Ensure uniform indentation using 4 spaces per indentation level without mixing tabs and spaces."
        if error_type == "TabError":
            return "Replace all tab characters with 4 spaces."
        if "unexpected EOF while parsing" in msg_lower:
            return "Ensure all parentheses, brackets, braces, and multi-line docstrings/strings are closed."
        if "was never closed" in msg_lower:
            return "Close the unclosed bracket or parenthesis on this or preceding lines."
        if "invalid syntax" in msg_lower and line_text and line_text.rstrip().endswith(("def", "if", "for", "while", "class", "else", "elif")):
            return "Add a colon (:) at the end of the compound statement header."
        return "Review the syntax at the highlighted line and column offset."

    @classmethod
    def validate_code_snippet(cls, source_code: str, filename: str = "<artifact>") -> ASTValidationResult:
        """Parse source code string via AST and return a structured ASTValidationResult."""
        if not source_code or not source_code.strip():
            return ASTValidationResult(is_valid=True, parsed_modules=[filename])

        diagnostics: List[SyntaxDiagnostic] = []

        try:
            ast.parse(source_code, filename=filename)
        except IndentationError as exc:
            line_text = exc.text if exc.text is not None else None
            diag = SyntaxDiagnostic(
                file_path=filename,
                error_type="IndentationError",
                line_number=exc.lineno,
                column_offset=exc.offset,
                offending_line=line_text.strip() if line_text else None,
                message=str(exc.msg) if hasattr(exc, "msg") else str(exc),
                suggestion=cls._extract_suggestion("IndentationError", str(exc), line_text),
            )
            diagnostics.append(diag)
        except SyntaxError as exc:
            line_text = exc.text if exc.text is not None else None
            diag = SyntaxDiagnostic(
                file_path=filename,
                error_type="SyntaxError",
                line_number=exc.lineno,
                column_offset=exc.offset,
                offending_line=line_text.strip() if line_text else None,
                message=str(exc.msg) if hasattr(exc, "msg") else str(exc),
                suggestion=cls._extract_suggestion("SyntaxError", str(exc), line_text),
            )
            diagnostics.append(diag)
        except Exception as exc:  # Catch other unexpected static compilation faults
            diagnostics.append(
                SyntaxDiagnostic(
                    file_path=filename,
                    error_type=type(exc).__name__,
                    line_number=None,
                    column_offset=None,
                    offending_line=None,
                    message=str(exc),
                    suggestion="Review code for structural and syntax anomalies.",
                )
            )

        is_valid = len(diagnostics) == 0
        return ASTValidationResult(
            is_valid=is_valid,
            diagnostics=diagnostics,
            parsed_modules=[filename] if is_valid else [],
        )

    @classmethod
    def validate_code_files(cls, files: Dict[str, str]) -> ASTValidationResult:
        """Validate multiple source files (mapping of filename -> code string)."""
        all_diagnostics: List[SyntaxDiagnostic] = []
        parsed_modules: List[str] = []

        for file_path, code in files.items():
            # Only validate Python files
            if not file_path.endswith((".py", ".pyw")):
                continue
            res = cls.validate_code_snippet(code, filename=file_path)
            if not res.is_valid:
                all_diagnostics.extend(res.diagnostics)
            else:
                parsed_modules.append(file_path)

        return ASTValidationResult(
            is_valid=len(all_diagnostics) == 0,
            diagnostics=all_diagnostics,
            parsed_modules=parsed_modules,
        )


def extract_python_blocks(markdown_or_code: str) -> Dict[str, str]:
    """
    Extract file paths and Python code from fenced markdown code blocks or plain code.
    Supports formats:
    - ```python path=foo.py
    - Plain Python source
    """
    files: Dict[str, str] = {}
    pattern = re.compile(r"```(?:python)?\s*(?:path=([^\s\n]+))?\n(.*?)```", re.DOTALL | re.IGNORECASE)
    matches = list(pattern.finditer(markdown_or_code))

    if matches:
        for idx, match in enumerate(matches):
            file_path = match.group(1) or f"artifact_{idx}.py"
            code_content = match.group(2)
            files[file_path] = code_content
    else:
        # If no code fence is present, treat entire content as single Python module
        files["main.py"] = markdown_or_code

    return files
