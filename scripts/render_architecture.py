#!/usr/bin/env python3
"""Render the architecture diagrams to docs/architecture/.

    python3 -m venv .tools/diagrams-venv && .tools/diagrams-venv/bin/pip install diagrams
    .tools/diagrams-venv/bin/python scripts/render_architecture.py

Needs Graphviz (`dot`) on the PATH. Two pictures:

  deployment.{png,svg}   what runs in Google Cloud (ks-opm-project, us-west1)
  application.{png,svg}  the ADK swarm, the shared scratchpad and the two stores

Every label is a fact from the Terraform module or the app; keep them in step
when either changes (sizes, model, agent roster).
"""
from __future__ import annotations

from pathlib import Path

import graphviz
from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.compute import Run
from diagrams.gcp.database import SQL, Memorystore
from diagrams.gcp.devtools import ContainerRegistry
from diagrams.gcp.ml import VertexAI
from diagrams.gcp.network import DNS, LoadBalancing, PrivateServiceConnect
from diagrams.gcp.security import IAP, SecretManager
from diagrams.onprem.client import Users
from diagrams.onprem.network import Internet

OUT = Path(__file__).resolve().parent.parent / "docs" / "architecture"
FONT = "Helvetica"
GREY = "#5f6368"
BLUE = "#1a73e8"
GREEN = "#188038"
RED = "#d93025"
YELLOW = "#f9ab00"


def deployment() -> None:
    graph_attr = {
        "fontname": FONT, "fontsize": "22", "labelloc": "t", "pad": "0.6",
        "nodesep": "0.5", "ranksep": "1.0", "splines": "spline", "bgcolor": "white", "newrank": "true",
    }
    node_attr = {"fontname": FONT, "fontsize": "12", "width": "1.6"}
    edge_attr = {"fontname": FONT, "fontsize": "11", "color": GREY, "fontcolor": GREY}
    cluster = {"fontname": FONT, "fontsize": "14", "style": "rounded", "color": "#dadce0", "bgcolor": "#fafafa"}

    with Diagram(
        "Travel Concierge Swarm - hosted on Google Cloud",
        filename=str(OUT / "deployment"), show=False, direction="LR",
        graph_attr=graph_attr, node_attr=node_attr, edge_attr=edge_attr, outformat=["png", "svg"],
    ):
        users = Users("Googlers\nany google.com account")
        lb = LoadBalancing("Optional (enable_load_balancer):\nglobal HTTPS LB - managed cert\ndemo-<DEMOID>.1987984870407.demo.altostrat.com")

        with Cluster("ks-opm-project - us-west1 - Terraform", graph_attr={**cluster, "margin": "28"}):
            iap = IAP("Identity-Aware Proxy\nGoogle-managed OAuth client\ngroup:everyone@google.com")
            secret = SecretManager("Secret Manager\ntravel-swarm-postgres-dsn")
            registry = ContainerRegistry("Artifact Registry\ntravel-swarm/app:<git sha>")

            with Cluster("Cloud Run - travel-swarm-app", graph_attr=cluster):
                run = Run("FastAPI + Google ADK swarm\n1 vCPU - 1 GiB - min 1 / max 10\nconcurrency 40 - SA travel-swarm-run")

            with Cluster("VPC travel-swarm-vpc - Direct VPC egress", graph_attr=cluster):
                psc_vk = PrivateServiceConnect("PSC endpoint\n10.10.0.4:6379")
                psc_pg = PrivateServiceConnect("PSC endpoint\n10.10.0.2:5432")

            valkey = Memorystore("Memorystore for Valkey 9.1\nSHARED_CORE_NANO - 1 shard\nsessions + scratchpad")
            postgres = SQL("Cloud SQL - PostgreSQL 17\ndb-g1-small\nsessions + scratchpad")
            vertex = VertexAI("Vertex AI - gemini-3.6-flash\nlocation global\nADC: no API key anywhere")

        search = Internet("Google Search\ngrounded research")

        users >> Edge(label="HTTPS - Google sign-in", color=BLUE, fontcolor=BLUE, penwidth="1.8") >> iap
        iap >> Edge(label="run.invoker for the IAP agent", color=BLUE, fontcolor=BLUE, penwidth="1.8") >> run
        users >> Edge(style="dashed", headport="w") >> lb >> Edge(style="dashed", label="IAP on the backend service") >> iap

        run >> Edge(label="redis://", color=RED, fontcolor=RED) >> psc_vk >> Edge(color=RED) >> valkey
        run >> Edge(label="postgresql://", color=GREEN, fontcolor=GREEN) >> psc_pg >> Edge(color=GREEN) >> postgres
        run >> Edge(label="~14 model calls per trip", color=YELLOW, fontcolor=GREY) >> vertex
        vertex >> Edge(label="grounding", color=YELLOW) >> search
        registry >> Edge(label="image", style="dashed") >> run
        secret >> Edge(label="POSTGRES_DSN", style="dashed") >> run


def application() -> None:
    g = graphviz.Digraph("application", format="png")
    g.attr(rankdir="TB", fontname=FONT, fontsize="22", labelloc="t", pad="0.5", nodesep="0.5",
           ranksep="0.75", splines="spline", bgcolor="white", newrank="true", compound="true",
           label="Travel Concierge Swarm - application architecture")
    g.attr("node", fontname=FONT, fontsize="12", shape="box", style="rounded,filled",
           fillcolor="white", color="#dadce0", margin="0.25,0.12")
    g.attr("edge", fontname=FONT, fontsize="11", color=GREY, fontcolor=GREY)

    g.node("browser", "Browser UI\nPlan a trip  |  Under the Hood\nDOM-only, strict CSP", fillcolor="#e8f0fe", color=BLUE)
    g.node("iap", "Identity-Aware Proxy\nany google.com account", fillcolor="#fef7e0", color=YELLOW)

    with g.subgraph(name="cluster_run") as run:
        run.attr(label="Cloud Run container - FastAPI (app/main.py)", fontname=FONT, fontsize="14",
                 style="rounded", color="#dadce0", bgcolor="#fafafa")
        run.node("api", "POST /api/run  ->  SSE stream\n"
                        "started - agent_step - blackboard - state_op - complete\n"
                        "GET /api/inspect/{run}  -  GET /api/health")
        run.node("telemetry", "Telemetry\nper-op instrumentation - op log\nlive feed for Under the Hood")

        with run.subgraph(name="cluster_swarm") as sw:
            sw.attr(label="Google ADK - SequentialAgent travel_swarm (six roles)",
                    fontname=FONT, fontsize="13", style="rounded", color="#c6dafc", bgcolor="#f3f8ff")
            sw.node("intake", "1  Supervisor - intake\nwrites the brief")
            with sw.subgraph(name="cluster_loop") as loop:
                loop.attr(label="2  LoopAgent plan_loop - up to 3 rounds", fontname=FONT, fontsize="12",
                          style="rounded,dashed", color="#a8c7fa", bgcolor="white")
                with loop.subgraph(name="cluster_par") as par:
                    par.attr(label="ParallelAgent research_fanout - three agents at once", fontname=FONT,
                             fontsize="12", style="rounded", color="#a8c7fa", bgcolor="#e8f0fe")
                    # Scout next to Stay so the shortlist hand-off is a short flat edge.
                    par.node("scout", "Destination & Vibe Scout\npublishes the shortlist")
                    par.node("stay", "Accommodation & Stay\nwaits for the shortlist")
                    par.node("transit", "Flight & Transit\nranks routes by price")
                loop.node("budget", "Budget Guardrail\nexits the loop when the plan fits")
            sw.node("itinerary", "3  Itinerary Assembly\nday-by-day timeline")
            sw.node("final", "4  Supervisor - finalize\nitinerary + summary")

        run.node("tools", "Scratchpad tools (ADK FunctionTool)\nintake - scout - flights - stays - budget - itinerary\n"
                          "every read/write measured: op, latency, bytes")
        run.node("blackboard", "Blackboard signals\nasyncio.Event set after the store write lands\n(waiters block, no polling)")
        run.node("registry", "Backend registry - one store per run\nValkeySessionService / PostgresSessionService\nValkeyScratchpad / PostgresScratchpad")

    g.node("gemini", "Vertex AI - gemini-3.6-flash\nagent reasoning + grounded research\n(Google Search tool)", fillcolor="#fef7e0", color=YELLOW)
    g.node("valkey", "Memorystore for Valkey\nADK session + events, scratchpad hashes\nTTL per run", fillcolor="#fce8e6", color=RED)
    g.node("postgres", "Cloud SQL - PostgreSQL 17\nsessions, events, scratchpad tables\nschema.sql applied at startup", fillcolor="#e6f4ea", color=GREEN)

    # Request path.
    g.edge("browser", "iap", label="HTTPS", color=BLUE, fontcolor=BLUE, penwidth="1.8")
    g.edge("iap", "api", color=BLUE, penwidth="1.8")
    g.edge("api", "browser", label="SSE events as they happen", style="dashed", color=BLUE, fontcolor=BLUE)
    g.edge("api", "intake", label="run_swarm(brief, backend)")

    # Agent order.
    for a in ("scout", "stay", "transit"):
        g.edge("intake", a)
        g.edge(a, "budget")
    # xlabel (not label): a labelled flat edge gets a virtual rank and loops above the fan-out.
    g.edge("scout", "stay", xlabel="shortlist via blackboard", style="dashed", constraint="false")
    g.edge("budget", "stay", label="over budget: next round", style="dashed", constraint="false",
           lhead="cluster_par")
    g.edge("budget", "itinerary")
    g.edge("itinerary", "final")

    # What the agents touch (drawn once per cluster, not per agent).
    g.edge("stay", "gemini", ltail="cluster_swarm", label="model calls, ~14 per trip",
           color=YELLOW, fontcolor=GREY, constraint="false")
    g.edge("final", "tools", ltail="cluster_swarm", label="tool calls")
    g.edge("tools", "blackboard", label="publish / await", style="dashed")
    g.edge("tools", "registry", label="scratchpad + session ops")
    g.edge("tools", "telemetry", style="dotted", constraint="false")
    g.edge("telemetry", "api", label="state_op + blackboard events", style="dotted", constraint="false")
    g.edge("registry", "valkey", label="backend=valkey", color=RED, fontcolor=RED, penwidth="1.6")
    g.edge("registry", "postgres", label="backend=postgres", color=GREEN, fontcolor=GREEN, penwidth="1.6")

    g.render(str(OUT / "application"), cleanup=True)
    g.format = "svg"
    g.render(str(OUT / "application"), cleanup=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    deployment()
    application()
    print("\n".join(str(p) for p in sorted(OUT.iterdir())))
