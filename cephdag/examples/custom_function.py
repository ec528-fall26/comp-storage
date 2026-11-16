"""Count log lines that contain a keyword: a data-dependent function, defined once.

The output size of `grep@1` depends on the data, so its declared ratio is only an
estimate for the planner; the worker records the real size and later decisions use it.
This module must be importable on every worker:

    python -m cephdag.worker --node 0 --functions examples.custom_function

and on the client, then:

    python -m examples.custom_function
"""

from cephdag import Client, Cluster, Graph, function


@function("grep", "1", inputs=["lines"], outputs=["lines"])
def keep_matching_lines(inputs, params, output_sizes):
    keyword = params["keyword"].encode()
    lines = inputs[0].splitlines(keepends=True)
    return (b"".join(line for line in lines if keyword in line),)


@function("count", "1", inputs=["lines"], outputs=["count"])
def count_lines(inputs, params, output_sizes):
    return (str(inputs[0].count(b"\n")).encode(),)


@function("add", "1", inputs=["count", "count"], outputs=["count"])
def add_counts(inputs, params, output_sizes):
    return (str(int(inputs[0]) + int(inputs[1])).encode(),)


def keyword_count_graph(dataset, keyword):
    graph = Graph()
    matching = graph.map("grep@1", dataset, ratios=[0.1], iterations=0, params={"keyword": keyword})
    counts = graph.map("count@1", matching, ratios=[0], iterations=0)
    total = graph.reduce("add@1", counts, ratios=[0], iterations=0)
    return graph, total


def main():
    client = Client(Cluster.load(), models="results/models.json")
    logs = [b"ok\nerror: disk\nok\n", b"error: net\nerror: cpu\n", b"ok\n"]
    dataset = client.put_dataset("example-logs", logs, dtype="lines")
    graph, _ = keyword_count_graph(dataset, "error")
    job = client.submit(graph, "greedy").wait(timeout=600)
    (total,) = job.outputs()
    print("lines with 'error':", int(client.read(total)))


if __name__ == "__main__":
    main()
