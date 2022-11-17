"""Source for architecture.png component diagram."""

import os

from diagrams import Cluster, Diagram
from diagrams.gcp.compute import KubernetesEngine
from diagrams.gcp.network import LoadBalancing
from diagrams.onprem.client import User
from diagrams.onprem.compute import Server

os.chdir(os.path.dirname(__file__))

graph_attr = {
    "label": "",
    "labelloc": "bbc",
    "nodesep": "0.2",
    "pad": "0.2",
    "ranksep": "0.75",
    "splines": "spline",
}

node_attr = {
    "fontsize": "12.0",
}

with Diagram(
    "datalinker architecture",
    show=False,
    filename="architecture",
    outformat="png",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    user = User("End user")

    with Cluster("Science Platform"):
        ingress = LoadBalancing("Ingress")
        gafaelfawr = KubernetesEngine("Authentication")
        tap = KubernetesEngine("TAP")
        datalinker = KubernetesEngine("datalinker")

    with Cluster("Data Storage"):
        qserv = Server("qserv")
        database = Server("Database")
        storage = Server("Object store")

    user >> ingress >> gafaelfawr >> [tap, datalinker]
    tap >> qserv >> database
    datalinker >> storage
    user >> storage
