#!/usr/bin/env python3
"""Render the agent-swarm diagram to docs/architecture/.

    python3 -m venv .tools/diagrams-venv && .tools/diagrams-venv/bin/pip install graphviz
    .tools/diagrams-venv/bin/python scripts/render_architecture.py

Needs Graphviz (`dot`) on the PATH. Output: application.{png,svg}.

This is the demo picture and it shows the agents only: the six roles, the
planning loop and the hand-offs between agents. The server, the scratchpad,
the stores and Gemini are deliberately left out (the README covers them).
Every word in the picture is still a fact about the app, so keep it in step
when the roster or the loop changes.
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
    "solid arrows: order of work &#160;&#183;&#160; dashed arrows: one agent waits for another's finding"
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
    g.node("end", "", shape="point", style="invis", width="0.02", height="0.02")

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
    g.node("final", "Supervisor\nfinal itinerary + summary", fillcolor="#e8f0fe", color=BLUE)

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
    g.edge("final", "end", label="   itinerary", color=BLUE, fontcolor=BLUE, penwidth="1.8",
           headclip="false")

    g.render(str(OUT / "application"), cleanup=True)
    g.format = "svg"
    g.render(str(OUT / "application"), cleanup=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    application()
    print("\n".join(str(p) for p in sorted(OUT.iterdir())))
