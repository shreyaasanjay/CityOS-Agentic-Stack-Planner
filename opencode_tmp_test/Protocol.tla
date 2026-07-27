---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS A, B, C

(* --algorithm Protocol {
variables
  bus = <<>>; \* A,B,C -> A,B,C, labels: ['x', 'y']
  R = "FREE"; \* Lock

macro send(ch, msg) {
  ch := Append(ch, msg);
}

macro receive(ch, var) {
  await Len(ch) > 0;
  var := Head(ch);
  ch := Tail(ch);
}

macro acquire_lock(lock) {
  await lock = "FREE";
  lock := self;
}

macro release_lock(lock) {
  lock := "FREE";
}

fair process (A_proc \in {A})
variables msg = "";
{
  A_start:
    skip; \* TODO: replace with A's protocol logic
  A_done:
    skip;
}

fair process (B_proc \in {B})
variables msg = "";
{
  B_start:
    skip; \* TODO: replace with B's protocol logic
  B_done:
    skip;
}

fair process (C_proc \in {C})
variables msg = "";
{
  C_start:
    skip; \* TODO: replace with C's protocol logic
  C_done:
    skip;
}

} *)

AllDone == \A p \in {A, B, C}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {A, B, C}: pc[p] \in STRING
  /\ R \in {A, B, C, "FREE"}
  /\ bus \in Seq(STRING)

NoOrphanLocks ==
  AllDone => (R = "FREE")

ChannelsDrained ==
  AllDone => (Len(bus) = 0)

ChannelBound ==
  Len(bus) <= 3

====