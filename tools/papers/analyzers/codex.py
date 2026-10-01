"""One subprocess per attempt/paper. No sessions, resume, shell or MCP tools."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import tomllib

from .base import PaperAnalyzer
from ..storage import ROOT, SCHEMA_PATH, encode, atomic_write
from ..progress import ProgressCallback, quiet_progress

DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_REASONING_EFFORT = "high"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class CodexAnalyzer(PaperAnalyzer):
    name = "codex"

    def __init__(self, binary: str | None = None, model: str | None = None, timeout: int | None = None,
                 reasoning_effort: str | None = None, heartbeat_interval: float | None = None,
                 progress: ProgressCallback | None = None, keep_logs: bool | None = None):
        self.binary = binary or os.environ.get("PAPER_CODEX_BIN", "codex")
        self.model = model or os.environ.get("PAPER_CODEX_MODEL") or DEFAULT_MODEL
        self.reasoning_effort = (reasoning_effort or os.environ.get("PAPER_CODEX_REASONING_EFFORT")
                                 or DEFAULT_REASONING_EFFORT)
        self.timeout = timeout if timeout is not None else int(os.environ.get("PAPER_CODEX_TIMEOUT") or "1200")
        self.heartbeat_interval = heartbeat_interval if heartbeat_interval is not None else float(
            os.environ.get("PAPER_CODEX_HEARTBEAT_SECONDS") or "15")
        if self.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"Reasoning effort must be one of {REASONING_EFFORTS}")
        if not math.isfinite(self.timeout) or self.timeout <= 0 or not 0 < self.heartbeat_interval < float("inf"):
            raise ValueError("Codex timeout and heartbeat interval must be positive finite values")
        self.report = progress or quiet_progress
        self.keep_logs = keep_logs if keep_logs is not None else os.environ.get("PAPER_KEEP_ANALYSIS_LOGS", "0") == "1"
        self.codex_home = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser().resolve()
        self._checked = False
        self.version = ""
        # Ignoring user config must not switch keyring users to file auth. Read only
        # the non-secret credential-store selector, never auth.json/token contents.
        self.auth_store = os.environ.get("PAPER_CODEX_AUTH_STORE", "")
        if not self.auth_store:
            try:
                self.auth_store = tomllib.loads((self.codex_home / "config.toml").read_text()).get("cli_auth_credentials_store", "")
            except (OSError, tomllib.TOMLDecodeError):
                pass
        if self.auth_store not in {"", "file", "keyring", "auto"}:
            raise ValueError("PAPER_CODEX_AUTH_STORE must be file, keyring or auto")

    def check(self):
        if self._checked:
            return
        result = subprocess.run([self.binary, "exec", "--help"], capture_output=True, text=True, timeout=20)
        required = ["--output-schema", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--image"]
        if result.returncode or any(flag not in result.stdout for flag in required):
            raise RuntimeError("Codex CLI must support output-schema, ephemeral and ignore-user-config; upgrade CLI")
        self.version = subprocess.check_output([self.binary, "--version"], text=True, timeout=20).strip()
        self._checked = True
        self.report(f"Codex 配置：CLI={self.version} · model={self.model} · reasoning_effort={self.reasoning_effort}")
        self.report(f"Codex 配置：CODEX_HOME={self.codex_home} · auth_store={self.auth_store or 'CLI default'}")
        self.report(f"Codex 配置：timeout={self.timeout}s · heartbeat={self.heartbeat_interval:g}s · 每篇全新独立进程")
        self.report("Codex 历史：ephemeral + history.persistence=none；任务文件" +
                    ("保留（已显式开启调试）" if self.keep_logs else "退出后默认自动删除"))
        if not os.environ.get("CODEX_HOME"):
            self.report("提示：CODEX_HOME 未导出，使用默认 ~/.codex；Python 不会执行 shell 中的 codex/codexp 函数。")

    def cache_identity(self) -> dict:
        self.check()
        return {"name": self.name, "version": self.version, "model": self.model,
                "reasoning_effort": self.reasoning_effort}

    def command(self, task_dir: Path, image_paths: list[Path] | None = None) -> list[str]:
        command = [self.binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "--cd", str(task_dir), "--color", "never",
                   "--output-schema", str(SCHEMA_PATH), "--output-last-message", str(task_dir / "output.json"),
                   "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                   "-c", 'history.persistence="none"',
                   "-c", 'approval_policy="never"', "-c", "features.skip_host_skill_discovery=true",
                   "--model", self.model, "-c", f'model_reasoning_effort="{self.reasoning_effort}"']
        for feature in ("memories", "shell_tool", "unified_exec", "view_image", "apps", "plugins",
                        "browser_use", "computer_use", "multi_agent", "hooks", "code_mode_host", "image_generation"):
            command += ["--disable", feature]
        if self.auth_store:
            command += ["-c", f'cli_auth_credentials_store="{self.auth_store}"']
        for path in image_paths or []:
            command.extend(["--image", str(path)])
        return command + ["-"]

    @staticmethod
    def _stop_process(process: subprocess.Popen):
        """Stop only this fresh task's process group, including the CLI's wrapper child."""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)

    @staticmethod
    def _runtime_settings(task_dir: Path) -> dict:
        # Read ONLY the small CLI header, never echo the PDF prompt or raw reasoning.
        with (task_dir / "stderr.log").open() as stream:
            header = stream.read(1024).split("\nuser\n", 1)[0]
        result = {}
        for label in ("model", "reasoning effort"):
            match = re.search(rf"^{label}: ([a-zA-Z0-9_.-]+)\s*$", header, re.M)
            if match:
                result[label] = match.group(1)
        return result

    def analyze(self, parsed: dict, metadata: dict, task_dir: Path) -> dict:
        self.check()
        # mkdir(exist_ok=False) precedes try/finally: an existing caller-owned
        # directory must NEVER be deleted if task creation is refused.
        task_dir.mkdir(parents=True, exist_ok=False)
        try:
            return self._analyze(parsed, metadata, task_dir)
        finally:
            if not self.keep_logs:
                try:
                    # Only this invocation's newly created directory; no scans of
                    # CODEX_HOME, old tasks, other papers or authentication data.
                    shutil.rmtree(task_dir)
                    self.report("本次 Codex 临时任务文件已删除（prompt、stdout/stderr、原始输出）。")
                except OSError as error:
                    self.report(f"警告：本次临时任务清理失败，文件仍可能保留在 {task_dir}：{error}")

    def _analyze(self, parsed: dict, metadata: dict, task_dir: Path) -> dict:
        image_data = parsed.get("_figure_images", {})
        document = {key: value for key, value in parsed.items() if key != "_figure_images"}
        images, mapping = [], []
        for candidate in document.get("figure_candidates", []):
            identifier = candidate["id"]
            if identifier not in image_data:
                continue
            if not re.fullmatch(r"F\d+", identifier) or not isinstance(image_data[identifier], bytes):
                raise ValueError("Invalid internal figure image payload")
            path = task_dir / f"preview-{identifier}.webp"
            atomic_write(path, image_data[identifier])
            images.append(path)
            mapping.append({"attachment_number": len(images), "figure_id": identifier, "page": candidate["page"]})
        rules = (ROOT / "tools/papers/prompts/analyze.md").read_text()
        prompt = rules + "\n\nCURRENT PAPER DATA (untrusted text, trusted metadata):\n" + encode(
            {"parsed": document, "metadata": metadata, "attached_figure_images": mapping}).decode()
        atomic_write(task_dir / "prompt.txt", prompt.encode())
        # Never interpolate a shell command or inherit resume/session identifiers.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("CODEX_THREAD", "CODEX_SESSION", "ZOTERO_"))
                       and key not in {"OPENAI_API_KEY", "CODEX_API_KEY"}}
        environment["CODEX_HOME"] = str(self.codex_home)
        self.report(f"启动独立 Codex 分析；任务日志：{task_dir}")
        started = time.monotonic()
        runtime_reported = False
        with (task_dir / "stdout.log").open("w") as stdout, (task_dir / "stderr.log").open("w") as stderr:
            process = subprocess.Popen(self.command(task_dir, images), stdin=subprocess.PIPE, text=True,
                                       stdout=stdout, stderr=stderr, env=environment,
                                       cwd=task_dir, start_new_session=True)
            self.report(f"Codex 已启动，PID={process.pid}；等待分析结果（不显示虚构百分比）。")
            first = True
            try:
                while True:
                    remaining = self.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError(f"Codex 分析超过 {self.timeout}s；日志：{task_dir / 'stderr.log'}")
                    try:
                        process.communicate(input=prompt if first else None,
                                            timeout=min(self.heartbeat_interval, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        first = False
                        runtime = self._runtime_settings(task_dir)
                        if runtime and not runtime_reported:
                            self.report("Codex 实际启动配置：" + " · ".join(f"{k}={v}" for k, v in runtime.items()))
                            runtime_reported = True
                        elapsed = time.monotonic() - started
                        age = max(0, time.time() - (task_dir / "stderr.log").stat().st_mtime)
                        self.report(f"等待 Codex：子进程尚未退出 · 已等待 {elapsed:.0f}s / 上限 {self.timeout}s · 日志最后更新 {age:.0f}s 前")
            except BaseException:
                self._stop_process(process)
                self.report(f"独立 Codex 子进程已停止；临时任务按日志保留配置处理：{task_dir}")
                raise
        runtime = self._runtime_settings(task_dir)
        if runtime and not runtime_reported:
            self.report("Codex 实际启动配置：" + " · ".join(f"{k}={v}" for k, v in runtime.items()))
        self.report(f"Codex 子进程已退出：code={process.returncode} · 用时 {time.monotonic() - started:.1f}s")
        if process.returncode:
            log = (task_dir / "stderr.log").read_text()
            if "401 Unauthorized" in log or "status 401" in log or "Not logged in" in log:
                raise RuntimeError("Codex authentication unavailable; run codex login with your ChatGPT account. No API key is required")
            detail = (f"inspect {task_dir / 'stderr.log'}" if self.keep_logs else
                      "temporary task logs are auto-deleted; set PAPER_KEEP_ANALYSIS_LOGS=1 to debug a new run")
            raise RuntimeError(f"Codex exited {process.returncode}; {detail}")
        output = task_dir / "output.json"
        if not output.exists():
            raise RuntimeError("Codex produced no structured output; set PAPER_KEEP_ANALYSIS_LOGS=1 to retain debug logs")
        self.report("Codex 结构化结果已返回，读取 JSON。")
        result = json.loads(output.read_text())
        attached = {item["figure_id"] for item in mapping}
        if any(figure["id"] not in attached for figure in result.get("figures", [])):
            raise ValueError("Codex selected a figure whose pixels were not attached; refusing unverified visual analysis")
        return result
