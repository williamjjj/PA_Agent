from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QWidget, QPlainTextEdit
from pa_agent.util.stream_batcher import StreamBatcher
from pa_agent.gui.stream_text import append_preview, configure_stream_edit, PREVIEW_MAX_CHARS


def test_100k_small_deltas_are_lossless_with_bounded_signal_count():
    events=[]
    batch=StreamBatcher(lambda stage,kind,text:events.append((stage,kind,text)),interval=100)
    for _ in range(100_000): batch.add("stage1","reasoning","中")
    batch.add("stage1","content","{}")
    batch.flush()
    assert ''.join(t for s,k,t in events if k=="reasoning")=="中"*100_000
    assert events[-1]==("stage1","content","{}")
    assert len(events)<30


def test_preview_bounds_single_line_not_just_block_count(qtbot):
    edit=QPlainTextEdit(); qtbot.addWidget(edit); configure_stream_edit(edit)
    for _ in range(100): append_preview(edit,"x"*4096)
    assert edit.document().characterCount() <= PREVIEW_MAX_CHARS+1
    assert not edit.isUndoRedoEnabled()


def test_chat_workers_release_qobjects_after_many_turns(qtbot):
    from pa_agent.gui.ai_stream_window import _ChatWorker
    from pa_agent.ai.deepseek_client import AIReply,AIUsage
    from pa_agent.util.threading import CancelToken
    class Session:
        def send(self,*args,**kwargs):
            for _ in range(800): kwargs['on_content_token']('x')
            return AIReply('x'*800,'',{},AIUsage(),'test',1)
    host=QWidget(); qtbot.addWidget(host)
    for _ in range(20):
        worker=_ChatWorker(Session(),'test',CancelToken(),parent=host)
        completed = []
        QThread.finished.__get__(worker).connect(lambda: completed.append(True))
        QThread.finished.__get__(worker).connect(worker.deleteLater)
        worker.start()
        # Observe the host, not a signal wrapper whose QObject deleteLater has
        # already freed before pytest-qt disconnects it (Qt 6.12 on Windows).
        qtbot.waitUntil(lambda:bool(completed) and len(host.findChildren(QThread))==0)
