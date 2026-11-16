import collections, sys
import rados
c = rados.Rados(conffile="/etc/ceph/ceph.conf")
c.connect()
total = collections.Counter()
count = collections.Counter()
for n in range(8):
    io = c.open_ioctx(f"node{n}")
    io.set_namespace(rados.LIBRADOS_ALL_NSPACES)
    for obj in io.list_objects():
        io.set_namespace(obj.nspace)
        size, _ = io.stat(obj.key)
        io.set_namespace(rados.LIBRADOS_ALL_NSPACES)
        parts = obj.key.split("/")
        prefix = "/".join(parts[:2]) if parts[0] != "runs" else "runs/" + parts[1][:40]
        total[prefix] += size
        count[prefix] += 1
for prefix, size in total.most_common(25):
    print(f"{size / 2**20:10.1f} MiB {count[prefix]:6d} {prefix}")
print(f"{sum(total.values()) / 2**30:.2f} GiB total")
