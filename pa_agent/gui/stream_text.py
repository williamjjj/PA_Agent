"""Bound the live preview, retaining the complete analysis in saved records."""
from PyQt6.QtGui import QTextCursor

PREVIEW_MAX_CHARS = 250_000


def configure_stream_edit(edit) -> None:
    edit.setUndoRedoEnabled(False)
    edit.document().setMaximumBlockCount(6000)
    edit.setToolTip("实时预览最多保留 25 万字符；完整分析请查看已保存的记录。")


def append_preview(edit, text: str, *, autoscroll: bool = True) -> None:
    cursor = QTextCursor(edit.document())
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(text)
    excess = edit.document().characterCount() - PREVIEW_MAX_CHARS - 1
    if excess > 0:
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.setPosition(excess, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
    if autoscroll:
        edit.verticalScrollBar().setValue(edit.verticalScrollBar().maximum())
