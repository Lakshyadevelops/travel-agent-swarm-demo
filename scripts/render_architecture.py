#!/usr/bin/env python3
"""Render the application architecture diagram to docs/architecture/.

    python3 -m venv .tools/diagrams-venv && .tools/diagrams-venv/bin/pip install graphviz
    .tools/diagrams-venv/bin/python scripts/render_architecture.py

Needs Graphviz (`dot`) on the PATH. Output: application.{png,svg}.

This is the demo picture, so it abstracts on purpose: the agent roles, the
planning loop, the shared scratchpad and the choice of store. Endpoint names,
event types and internal modules stay out; every word that is in the picture
is still a fact about the app, so keep it in step when the roster, the loop
or the stores change.
"""
from __future__ import annotations

from pathlib import Path

import graphviz

OUT = Path(__file__).resolve().parent.parent / "docs" / "architecture"
FONT = "Helvetica"
GREY = "#5f6368"
BLUE = "#1a73e8"
GREEN = "#188038"
RED = "#d93025"
YELLOW = "#f9ab00"

TITLE = (
    '<<FONT POINT-SIZE="22">Travel Concierge Swarm &#8211; how a trip gets planned</FONT>'
    '<BR/><BR/><FONT POINT-SIZE="12" COLOR="#5f6368">'
    "solid arrows: order of work &#160;&#183;&#160; dashed: agents coordinating through the scratchpad "
    "&#160;&#183;&#160; a run keeps all of its state in one store, Valkey or PostgreSQL"
    "</FONT><BR/><BR/>>"
)


def application() -> None:
    g = graphviz.Digraph("application", format="png")
    g.attr(rankdir="TB", fontname=FONT, labelloc="t", label=TITLE, pad="0.5", nodesep="0.9",
           ranksep="0.65", splines="spline", bgcolor="white", newrank="true", compound="true")
    g.attr("node", fontname=FONT, fontsize="12", shape="box", style="rounded,filled",
           fillcolor="white", color="#dadce0", margin="0.25,0.12")
    g.attr("edge", fontname=FONT, fontsize="11", color=GREY, fontcolor=GREY)

    g.node("user", "Traveler's browser\nPlan a trip  |  Under the Hood", fillcolor="#e8f0fe", color=BLUE)
    g.node("app", "FastAPI server\nruns the swarm, streams progress to the page")
    g.node("gemini", "Gemini\nevery agent reasons with it;\nresearch is grounded with Google Search",
           fillcolor="#fef7e0", color=YELLOW)

    with g.subgraph(name="cluster_swarm") as sw:
        sw.attr(label="Agent swarm - Google ADK, six roles, one shared scratchpad", fontname=FONT,
                fontsize="14", style="rounded", color="#c6dafc", bgcolor="#f3f8ff")
        # `gport` is an invisible anchor left of the Supervisor: the Gemini
        # connector starts there, so it visibly comes out of the swarm box.
        with sw.subgraph() as top:
            top.attr(rank="same")
            top.node("gport", "", shape="point", style="invis", width="0.02", height="0.02")
            top.node("intake", "Supervisor\nturns the request into a brief")
        with sw.subgraph(name="cluster_loop") as loop:
            loop.attr(label="Planning loop - up to 3 rounds", fontname=FONT, fontsize="12",
                      style="rounded,dashed", color="#a8c7fa", bgcolor="white")
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
        sw.node("itinerary", "Itinerary Agent\nday-by-day timeline")
        sw.node("final", "Supervisor\nfinal itinerary + summary")
        # The scratchpad is drawn as a layer under all the agents, as wide as the
        # box, so it reads as shared by every role rather than owned by one.
        sw.node("pad", "Shared scratchpad - every agent posts its findings here and waits for the others'\n"
                       "every read and write is timed",
                width="11", fillcolor="#f1f3f4", color=GREY, penwidth="1.4")

    g.node("valkey", "Valkey\nMemorystore", fillcolor="#fce8e6", color=RED)
    g.node("postgres", "PostgreSQL\nCloud SQL", fillcolor="#e6f4ea", color=GREEN)

    # Request in, live progress out (ports keep the pair side by side instead of curling).
    g.edge("user", "app", label="trip request + which store to use  ", color=BLUE, fontcolor=BLUE,
           penwidth="1.8", tailport="sw", headport="nw")
    g.edge("app", "user", label="  live: every agent step and store operation,\n  then the itinerary",
           style="dashed", color=BLUE, fontcolor=BLUE, tailport="ne", headport="se")
    g.edge("user", "gemini", style="invis", weight="0")  # Gemini at the server's rank
    g.edge("app", "intake", label="brief")

    # Order of work. Compound edges touch the parallel block as one unit. The
    # row is rank=same, so scout -> stay -> transit are flat edges (tail left of
    # head) and pin the order; extra invisible fan-out edges would scramble it.
    g.edge("intake", "stay", lhead="cluster_par")
    g.edge("stay", "budget", ltail="cluster_par")
    # xlabel (not label): a labelled flat edge gets a virtual rank and loops above the block.
    g.edge("scout", "stay", xlabel="shortlist", style="dashed")
    g.edge("stay", "transit", xlabel="hotel", style="dashed")
    g.edge("budget", "stay", label="over budget: price caps, next round", style="dashed",
           constraint="false", lhead="cluster_par", tailport="e", headport="e")
    g.edge("budget", "itinerary", label="fits")
    g.edge("itinerary", "final")

    # What the agents lean on (drawn once for the swarm, not per agent).
    g.edge("gport", "intake", style="invis")  # flat edge: anchor left of the Supervisor
    g.edge("gemini", "gport", dir="back", color=YELLOW, penwidth="1.6", headclip="false")
    g.edge("final", "pad", style="invis")  # keeps the scratchpad layer at the bottom of the box
    g.edge("pad", "valkey", label="run on Valkey", color=RED, fontcolor=RED, penwidth="1.6")
    g.edge("pad", "postgres", label="run on PostgreSQL", color=GREEN, fontcolor=GREEN, penwidth="1.6")

    g.render(str(OUT / "application"), cleanup=True)
    g.format = "svg"
    g.render(str(OUT / "application"), cleanup=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    application()
    print("\n".join(str(p) for p in sorted(OUT.iterdir())))
