"""NL-R2-36: replay the equalDOF ties recorded in model_opensees.py.

The HR engine writes `ops.equalDOF(master, slave, dofs...)` when a custom_build ties two coincident nodes together --
e.g. Ex20 (October 2026 design): the transfer truss's bottom chord is a flexural element whose end nodes are tied in
all six DOFs to the truss nodes, so the truss carries the axial force and the chord element the Level-2 floor
bending. Every model the nonlinear tools rebuild (the elastic replay for the gravity axials, the IMK and fibre
nonlinear models, the DDM GMNIA model) must keep those ties; without them the chord element hangs free and the
gravity solve is singular (`elastic gravity analysis failed (-3)`).

Rules:
  * the LAST build of the export is used (package_reader / ingest clear the list at every `wipe`); repeated ties are
    applied once;
  * a tie is applied only when both nodes exist in the rebuilt model;
  * a tie whose slave (constrained) node is also a rigidDiaphragm slave is NOT applied (a node cannot be constrained
    twice under the Transformation handler; the engine never writes one) -- it is reported instead.
Skipped ties are returned so the caller can put them in its model warnings."""


def normalise(raw_ties):
    """[(master, slave, (dofs...))] from raw `equalDOF` argument lists, duplicates removed, order kept."""
    out, seen = [], set()
    for a in raw_ties or []:
        try:
            mst, slv = int(a[0]), int(a[1])
            dofs = tuple(int(d) for d in a[2:])
        except (TypeError, ValueError, IndexError):
            continue
        if not dofs:
            continue
        key = (mst, slv, dofs)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def apply(ops, raw_ties, rd_slaves=()):
    """Apply the ties to the current OpenSees domain. Returns (n_applied, [skipped messages])."""
    ties = normalise(raw_ties)
    if not ties:
        return 0, []
    have = set(ops.getNodeTags())
    rd = set(rd_slaves or ())
    done, skipped = 0, []
    for mst, slv, dofs in ties:
        if mst not in have or slv not in have:
            skipped.append("equalDOF(%d, %d): node %s not in the rebuilt model" % (mst, slv, mst if mst not in have else slv))
            continue
        if slv in rd:
            skipped.append("equalDOF(%d, %d): constrained node is also a rigid-diaphragm slave -- not applied" % (mst, slv))
            continue
        ops.equalDOF(mst, slv, *dofs)
        done += 1
    return done, skipped
