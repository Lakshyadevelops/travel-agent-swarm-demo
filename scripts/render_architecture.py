#!/usr/bin/env python3
"""Render the agent-swarm diagram to docs/architecture/.

    python3 -m venv .tools/diagrams-venv && .tools/diagrams-venv/bin/pip install graphviz
    .tools/diagrams-venv/bin/python scripts/render_architecture.py

Needs Graphviz (`dot`) on the PATH. Output: application.{png,svg}.

This is the demo picture: the six agent roles, the planning loop, the
hand-offs between agents, and underneath them the shared state every agent
reads and writes (scratchpad + ADK session storage, kept in the run's store).
The server, store internals and Gemini are deliberately left out (the README
covers them). Every word in the picture is still a fact about the app, so
keep it in step when the roster, the loop or the state layout changes.
"""
from __future__ import annotations

from pathlib import Path

import graphviz

OUT = Path(__file__).resolve().parent.parent / "docs" / "architecture"
FONT = "Helvetica"
GREY = "#5f6368"
BLUE = "#1a73e8"

TITLE = (
    '<<FONT POINT-SIZE="22">Travel Concierge Swarm &#8211; six agent roles on Google ADK</FONT>'
    '<BR/><BR/><FONT POINT-SIZE="12" COLOR="#5f6368">'
    "solid arrows: order of work &#160;&#183;&#160; "
    "dashed arrows: one agent waits for another's finding, posted on the shared scratchpad"
    "</FONT><BR/><BR/>>"
)


def application() -> None:
    g = graphviz.Digraph("application", format="png")
    g.attr(rankdir="TB", fontname=FONT, labelloc="t", label=TITLE, pad="0.5", nodesep="0.7",
           ranksep="0.6", splines="spline", bgcolor="white", newrank="true", compound="true")
    g.attr("node", fontname=FONT, fontsize="12", shape="box", style="rounded,filled",
           fillcolor="white", color="#dadce0", margin="0.25,0.12")
    g.attr("edge", fontname=FONT, fontsize="11", color=GREY, fontcolor=GREY)

    # Invisible end points so the flow has a visible start and finish.
    g.node("start", "", shape="point", style="invis", width="0.02", height="0.02")

    g.node("intake", "Supervisor\nturns the request into a brief", fillcolor="#e8f0fe", color=BLUE)
    with g.subgraph(name="cluster_loop") as loop:
        loop.attr(label="Planning loop - up to 3 rounds", fontname=FONT, fontsize="13",
                  style="rounded,dashed", color="#a8c7fa", bgcolor="#fafbff")
        with loop.subgraph(name="cluster_par") as par:
            par.attr(label="Research - three agents at once", fontname=FONT, fontsize="12",
                     style="rounded", color="#a8c7fa", bgcolor="#e8f0fe")
            # One rank, in this order: Scout next to Stay so the shortlist hand-off is short.
            with par.subgraph() as row:
                row.attr(rank="same")
                row.node("scout", "Destination Scout\nwhere to base, what to see")
                row.node("stay", "Stay Agent\na hotel in the scout's pick")
                row.node("transit", "Transit Agent\nflights and the airport transfer")
        loop.node("budget", "Budget Guardrail\nprices the whole plan")
    g.node("itinerary", "Itinerary Agent\nday-by-day timeline")
    # The exit arrow leaves sideways so the state layer below is not read as the next step.
    with g.subgraph() as last:
        last.attr(rank="same")
        last.node("final", "Supervisor\nfinal itinerary + summary", fillcolor="#e8f0fe", color=BLUE)
        last.node("end", "", shape="point", style="invis", width="0.02", height="0.02")

    # Shared state: a layer under the agents rather than an arrow from one of
    # them, so it reads as used by every role. Both halves live in the store
    # picked for the run.
    with g.subgraph(name="cluster_state") as state:
        state.attr(label="Shared state - read and written by every agent - kept in the run's store, Valkey or PostgreSQL",
                   fontname=FONT, fontsize="12", style="rounded", color="#dadce0", bgcolor="#f8f9fa")
        with state.subgraph() as cells:
            cells.attr(rank="same")
            cells.node("pad", "Shared scratchpad\nfindings and hand-offs: shortlist, hotel, price caps", width="4.1")
            # Invisible anchor between the cells: the layer is centred on it (see below).
            cells.node("mid", "", shape="point", style="invis", width="0.02", height="0.02")
            cells.node("session", "Session storage\nthe ADK session and its event log", width="4.1")

    # Order of work. Compound edges touch the parallel block as one unit. The
    # row is rank=same, so scout -> stay -> transit are flat edges (tail left of
    # head) and pin the order; extra invisible fan-out edges would scramble it.
    g.edge("start", "intake", label="   trip request", color=BLUE, fontcolor=BLUE, penwidth="1.8",
           tailclip="false")
    g.edge("intake", "stay", lhead="cluster_par", label="brief")
    g.edge("stay", "budget", ltail="cluster_par")
    # xlabel (not label): a labelled flat edge gets a virtual rank and loops above the block.
    g.edge("scout", "stay", xlabel="shortlist", style="dashed")
    g.edge("stay", "transit", xlabel="hotel", style="dashed")
    g.edge("budget", "stay", label="over budget: price caps, next round", style="dashed",
           constraint="false", lhead="cluster_par", tailport="e", headport="e")
    g.edge("budget", "itinerary", label="fits")
    g.edge("itinerary", "final")
    g.edge("final", "end", xlabel="itinerary  ", color=BLUE, fontcolor=BLUE, penwidth="1.8")

    # Layout only: the state layer is centred under the last agent through the
    # `mid` anchor (strong pull); the light pulls on the cells keep them close.
    # Creation order (pad, mid, session) gives the left-to-right order.
    g.edge("final", "pad", style="invis")
    g.edge("final", "mid", style="invis", weight="20")
    g.edge("final", "session", style="invis")

    g.render(str(OUT / "application"), cleanup=True)
    g.format = "svg"
    g.render(str(OUT / "application"), cleanup=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    application()
    print("\n".join(str(p) for p in sorted(OUT.iterdir())))
