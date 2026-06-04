#!/usr/bin/env -S uv run -q --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["psutil>=7", "rich>=14", "typer>=0.16"]
# ///
"""Identify CPU-hungry VS Code extensions via watchdog logs, saved exthost profiles, and live process sampling. Installed verbatim to ~/.local/bin; uv resolves the inline dependencies on first run."""

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import psutil
import typer
from rich.console import Console
from rich.filesize import decimal as format_filesize
from rich.table import Table

SESSION_DIR_PATTERN = re.compile(r"\d{8}T\d{6}")
WINDOW_DIR_PATTERN = re.compile(r"window\d+")
EXTHOST_PID_PATTERN = re.compile(r"Started local extension host with pid (\d+)\.")
WATCHDOG_BLAME_PATTERN = re.compile(r"UNRESPONSIVE extension host: '([^']+)' took ([0-9.]+)% of [0-9.]+ms")
EXTENSION_DIR_PATTERN = re.compile(r"/extensions/([^/]+)/")
BUILTIN_EXTENSIONS_PATH = "/resources/app/extensions/"
PROFILE_GLOB = "exthost-*.cpuprofile"
BUSY_CPU_PERCENT = 50
MANUAL_PROFILE_HINT = (
    'profile manually: run "Developer: Show Running Extensions" > "Start Extension Host Profile" in VS Code, '
    "stop it after ~10s, then re-run this script to analyze the saved profile."
)

console = Console(highlight=False)
app = typer.Typer(add_completion=False)


class Variant(StrEnum):
    code = "code"
    insiders = "insiders"
    all = "all"

    @property
    def config_dir_names(self) -> tuple[str, ...]:
        match self:
            case Variant.code:
                return ("Code",)
            case Variant.insiders:
                return ("Code - Insiders",)
            case Variant.all:
                return ("Code", "Code - Insiders")


@dataclass(slots=True)
class BlameTally:
    blame_count: int = 0
    pct_total: float = 0.0

    @property
    def pct_average(self) -> float:
        return self.pct_total / self.blame_count


@dataclass(slots=True)
class VariantDiagnostics:
    config_dir_name: str
    live_exthost_pids: set[int] = field(default_factory=set)
    blame_tallies: dict[str, BlameTally] = field(default_factory=dict)


def list_renderer_logs(logs_dir: Path, session_count: int):
    sessions = sorted(
        (d for d in logs_dir.iterdir() if d.is_dir() and SESSION_DIR_PATTERN.fullmatch(d.name)),
        key=lambda d: d.name,
        reverse=True,
    )
    for session_dir in sessions[:session_count]:
        for window_dir in sorted(d for d in session_dir.iterdir() if d.is_dir() and WINDOW_DIR_PATTERN.fullmatch(d.name)):
            renderer_log = window_dir / "renderer.log"
            if renderer_log.is_file():
                yield renderer_log


def parse_renderer_log(renderer_log: Path) -> tuple[list[int], list[tuple[str, float]]]:
    text = renderer_log.read_text(errors="replace")
    exthost_pids = [int(m.group(1)) for m in EXTHOST_PID_PATTERN.finditer(text)]
    blames = [(m.group(1), float(m.group(2))) for m in WATCHDOG_BLAME_PATTERN.finditer(text)]
    return exthost_pids, blames


def is_live_exthost(pid: int) -> bool:
    try:
        cmdline = " ".join(psutil.Process(pid).cmdline())
    except psutil.Error:
        return False
    return "code" in cmdline and "node.mojom.NodeService" in cmdline


def collect_variant_diagnostics(config_dir_name: str, session_count: int) -> VariantDiagnostics | None:
    logs_dir = Path.home() / ".config" / config_dir_name / "logs"
    if not logs_dir.is_dir():
        return None
    diagnostics = VariantDiagnostics(config_dir_name)
    candidate_pids: set[int] = set()
    for renderer_log in list_renderer_logs(logs_dir, session_count):
        exthost_pids, blames = parse_renderer_log(renderer_log)
        candidate_pids.update(exthost_pids)
        for extension_id, pct in blames:
            tally = diagnostics.blame_tallies.setdefault(extension_id, BlameTally())
            tally.blame_count += 1
            tally.pct_total += pct
    diagnostics.live_exthost_pids = {pid for pid in candidate_pids if is_live_exthost(pid)}
    return diagnostics


def sample_cpu_percents(pids: set[int], sample_seconds: float) -> dict[int, float]:
    primed: list[psutil.Process] = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            process.cpu_percent()
            primed.append(process)
        except psutil.Error:
            continue
    time.sleep(sample_seconds)
    percents: dict[int, float] = {}
    for process in primed:
        try:
            percents[process.pid] = process.cpu_percent()
        except psutil.Error:
            continue
    return percents


def read_rss_bytes(pid: int) -> int:
    try:
        return psutil.Process(pid).memory_info().rss
    except psutil.Error:
        return 0


def classify_frame(url: str, function_name: str) -> str | None:
    if function_name == "(idle)":
        return None
    if function_name == "(garbage collector)":
        return function_name
    if extension_dir := EXTENSION_DIR_PATTERN.search(url):
        prefix = "builtin:" if BUILTIN_EXTENSIONS_PATH in url else ""
        return prefix + extension_dir.group(1)
    return "(vscode core / runtime)"


def attribute_profile_hits(profile_path: Path) -> Counter[str]:
    try:
        profile = json.loads(profile_path.read_text())
    except OSError, json.JSONDecodeError:
        return Counter()
    hits: Counter[str] = Counter()
    for node in profile.get("nodes", []):
        if not (hit_count := node.get("hitCount", 0)):
            continue
        frame = node.get("callFrame", {})
        if bucket := classify_frame(frame.get("url", ""), frame.get("functionName", "")):
            hits[bucket] += hit_count
    return hits


def print_variant_report(diagnostics: VariantDiagnostics, cpu_percents: dict[int, float]) -> None:
    console.print(f"[bold]== {diagnostics.config_dir_name} ==[/]")
    if not diagnostics.live_exthost_pids:
        console.print("  no running extension host", style="dim")
    for pid in sorted(diagnostics.live_exthost_pids):
        cpu = cpu_percents.get(pid)
        cpu_label = f"{cpu:.0f}% cpu" if cpu is not None else "cpu n/a"
        cpu_style = "red" if (cpu or 0) > BUSY_CPU_PERCENT else "green"
        console.print(f"  extension host pid {pid}: [{cpu_style}]{cpu_label}[/], rss {format_filesize(read_rss_bytes(pid))}")
    if diagnostics.blame_tallies:
        blame_table = Table(
            "blames", "extension", "avg % of blocked window", box=None, pad_edge=False, title="  watchdog blames (renderer.log)", title_justify="left"
        )
        ranked = sorted(diagnostics.blame_tallies.items(), key=lambda item: item[1].blame_count, reverse=True)
        for extension_id, tally in ranked:
            blame_table.add_row(f"{tally.blame_count}x", extension_id, f"{tally.pct_average:.0f}%")
        console.print(blame_table)
    else:
        console.print("  no watchdog blames found", style="dim")
    console.print()


def print_profiles_report(top_count: int) -> bool:
    profile_paths = sorted(Path("/tmp").glob(PROFILE_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if not profile_paths:
        console.print("no saved profiles in /tmp (the watchdog only saves them when the host blocks >~3s)", style="dim")
        return False
    files_table = Table(
        "profile",
        "saved",
        "top consumer",
        box=None,
        pad_edge=False,
        title=f"== saved profiles ({len(profile_paths)} files in /tmp) ==",
        title_justify="left",
        title_style="bold",
    )
    combined_hits: Counter[str] = Counter()
    for path in profile_paths:
        hits = attribute_profile_hits(path)
        if not hits:
            continue
        combined_hits.update(hits)
        top_bucket, top_hits = hits.most_common(1)[0]
        saved_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")
        files_table.add_row(path.name, saved_at, f"{100 * top_hits / hits.total():.0f}% {top_bucket}")
    console.print(files_table)
    attribution_table = Table("share", "consumer", box=None, pad_edge=False, title="  combined attribution (idle excluded)", title_justify="left")
    for bucket, hit_count in combined_hits.most_common(top_count):
        attribution_table.add_row(f"{100 * hit_count / combined_hits.total():.1f}%", bucket)
    console.print(attribution_table)
    console.print()
    return True


@app.command(epilog=f"For spinners that yield (never block the host) the watchdog stays silent — {MANUAL_PROFILE_HINT}")
def main(
    variant: Annotated[Variant, typer.Option(help="which VS Code build to inspect")] = Variant.all,
    sessions: Annotated[int, typer.Option(help="recent log sessions to scan")] = 2,
    sample_seconds: Annotated[float, typer.Option(help="live CPU sampling window")] = 3.0,
    top: Annotated[int, typer.Option(help="rows in the combined attribution")] = 8,
) -> None:
    """Identify CPU-hungry VS Code extensions via watchdog logs, saved exthost profiles, and live process sampling."""
    all_diagnostics = [diagnostics for name in variant.config_dir_names if (diagnostics := collect_variant_diagnostics(name, sessions))]
    if not all_diagnostics:
        console.print("no VS Code config directories found", style="red")
        raise typer.Exit(1)

    pids_to_sample = set().union(*(d.live_exthost_pids for d in all_diagnostics))
    cpu_percents = sample_cpu_percents(pids_to_sample, sample_seconds) if pids_to_sample else {}

    for diagnostics in all_diagnostics:
        print_variant_report(diagnostics, cpu_percents)
    has_profiles = print_profiles_report(top)

    has_blames = any(d.blame_tallies for d in all_diagnostics)
    is_burning = any(pct > BUSY_CPU_PERCENT for pct in cpu_percents.values())
    if is_burning and not (has_blames or has_profiles):
        console.print(f"extension host is busy but nothing is blamed: the offender yields between iterations — {MANUAL_PROFILE_HINT}")


if __name__ == "__main__":
    app()
