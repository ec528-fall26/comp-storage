"""Map and reduce a small float32 dataset on the lab with the builtin numeric functions.

Run from inside the lab network (for example on node 0):

    python -m examples.quickstart
"""

from cephdag import Client, Cluster, Graph
from cephdag.functions import synthetic


def main():
    client = Client(Cluster.load(), models="results/models.json")
    name = "quickstart"
    try:
        data = client.dataset(name)
    except client.store.not_found:
        data = client.put_dataset(name, [synthetic(4 * 2**20, seed) for seed in range(6)])
    graph = Graph()
    mapped = graph.map("map@1", data, iterations=32, ratios=[0.5])
    graph.reduce("join@1", mapped, iterations=1, ratios=[0.5])
    job = client.submit(graph, "heft")
    job.wait(timeout=600)
    metrics = job.metrics()
    print(f"{metrics['wall_seconds']:.2f} s, planning {metrics['scheduler_seconds']:.2f} s")
    for ref in job.outputs():
        print(ref.pool, ref.key, len(client.read(ref)), "bytes")


if __name__ == "__main__":
    main()
