---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS Occupancy_answer_agent

(* --algorithm Protocol {
variables
  \* No shared variables

fair process (occupancy_answer_agent_proc \in {Occupancy_answer_agent})
variables verified = "", answer_ready = FALSE;
{
  \* ---- Role: occupancy_context (gather privacy-safe structured context) ----
  oa_gather_camera_occupancy:
    skip; \* domain: get_camera_occupancy_context(sensor_id="camera_door_01") [tool: get_camera_occupancy_context(sensor_id: string) -> occupancy_context_packet; impl: external]
  oa_gather_entry_events:
    skip; \* domain: get_entry_event_context(sensor_id="camera_door_01") [tool: get_entry_event_context(sensor_id: string) -> entry_event_packet; impl: external]
  oa_gather_exit_events:
    skip; \* domain: get_exit_event_context(sensor_id="camera_door_01") [tool: get_exit_event_context(sensor_id: string) -> exit_event_packet; impl: external]
  oa_gather_anonymous_tracks:
    skip; \* domain: get_anonymous_track_context(sensor_id="camera_room_01") [tool: get_anonymous_track_context(sensor_id: string) -> track_context_packet; impl: external]
  oa_gather_motion_events:
    skip; \* domain: get_motion_event_context(sensor_id="camera_room_01") [tool: get_motion_event_context(sensor_id: string) -> motion_event_packet; impl: external]
  oa_gather_camera_coverage:
    skip; \* domain: get_camera_coverage_metadata(space_id="smart_room_1") [tool: get_camera_coverage_metadata(space_id: string) -> coverage_metadata_packet; impl: external]

  \* ---- Role: answer_synthesis ----
  oa_synthesize:
    skip; \* domain: answer_synthesis_harness(context_packets=<gathered>, contract="direct_answer_v1") [tool: answer_synthesis_harness(context_packets: seq, contract: string) -> direct_answer_v1; impl: local]

  \* ---- Verification coordination pattern (self-verification gate) ----
  \* Three-way nondeterministic outcome; TLC explores pass / privacy_violation / insufficient_evidence.
  oa_verify:
    either {
      oa_verify_pass_privacy:
        skip; \* domain: verify_privacy_scope(answer, privacy_constraints) [tool: verify_privacy_scope(answer: direct_answer_v1, privacy_constraints: seq) -> bool; impl: local]
      oa_verify_pass_evidence:
        skip; \* domain: verify_evidence_refs(answer) [tool: verify_evidence_refs(answer: direct_answer_v1) -> bool; impl: local]
      oa_verify_pass_fields:
        skip; \* domain: verify_required_fields(answer, required_fields) [tool: verify_required_fields(answer: direct_answer_v1, required_fields: seq) -> bool; impl: local]
        verified := "pass";
    } or {
      oa_verify_fail:
        either {
          oa_verify_violation:
            skip; \* domain: verify_privacy_scope(answer, privacy_constraints) [tool: verify_privacy_scope(answer: direct_answer_v1, privacy_constraints: seq) -> bool; impl: local]
            verified := "violation";
        } or {
          oa_verify_insufficient:
            skip; \* domain: verify_evidence_refs(answer) [tool: verify_evidence_refs(answer: direct_answer_v1) -> bool; impl: local]
            verified := "insufficient";
        };
    };

  \* ---- Decision: dispatch on verification outcome ----
  oa_decide:
    if (verified = "pass") {
      goto oa_emit_answer;
    } else {
      if (verified = "violation") {
        goto oa_synthesize;   \* open regeneration loop; TLC cycle detection bounds state space
      } else {
        goto oa_emit_insufficient;
      };
    };

  oa_emit_answer:
    skip; \* domain: emit_answer_packet(answer, contract="direct_answer_v1") [tool: emit_answer_packet(answer: direct_answer_v1, contract: string) -> answer_packet; impl: local]
    answer_ready := TRUE;
    goto occupancy_answer_agent_done;

  oa_emit_insufficient:
    skip; \* domain: emit_insufficient_evidence(contract="direct_answer_v1", fallback_type="insufficient_evidence") [tool: emit_insufficient_evidence(contract: string, fallback_type: string) -> fallback_packet; impl: local]
    answer_ready := TRUE;
    goto occupancy_answer_agent_done;

  occupancy_answer_agent_done:
    skip;
}

} *)
\* BEGIN TRANSLATION (chksum(pcal) = "f078c10b" /\ chksum(tla) = "57658e4a")
VARIABLES pc, verified, answer_ready

vars == << pc, verified, answer_ready >>

ProcSet == ({Occupancy_answer_agent})

Init == (* Process occupancy_answer_agent_proc *)
        /\ verified = [self \in {Occupancy_answer_agent} |-> ""]
        /\ answer_ready = [self \in {Occupancy_answer_agent} |-> FALSE]
        /\ pc = [self \in ProcSet |-> "oa_gather_camera_occupancy"]

oa_gather_camera_occupancy(self) == /\ pc[self] = "oa_gather_camera_occupancy"
                                    /\ TRUE
                                    /\ pc' = [pc EXCEPT ![self] = "oa_gather_entry_events"]
                                    /\ UNCHANGED << verified, answer_ready >>

oa_gather_entry_events(self) == /\ pc[self] = "oa_gather_entry_events"
                                /\ TRUE
                                /\ pc' = [pc EXCEPT ![self] = "oa_gather_exit_events"]
                                /\ UNCHANGED << verified, answer_ready >>

oa_gather_exit_events(self) == /\ pc[self] = "oa_gather_exit_events"
                               /\ TRUE
                               /\ pc' = [pc EXCEPT ![self] = "oa_gather_anonymous_tracks"]
                               /\ UNCHANGED << verified, answer_ready >>

oa_gather_anonymous_tracks(self) == /\ pc[self] = "oa_gather_anonymous_tracks"
                                    /\ TRUE
                                    /\ pc' = [pc EXCEPT ![self] = "oa_gather_motion_events"]
                                    /\ UNCHANGED << verified, answer_ready >>

oa_gather_motion_events(self) == /\ pc[self] = "oa_gather_motion_events"
                                 /\ TRUE
                                 /\ pc' = [pc EXCEPT ![self] = "oa_gather_camera_coverage"]
                                 /\ UNCHANGED << verified, answer_ready >>

oa_gather_camera_coverage(self) == /\ pc[self] = "oa_gather_camera_coverage"
                                   /\ TRUE
                                   /\ pc' = [pc EXCEPT ![self] = "oa_synthesize"]
                                   /\ UNCHANGED << verified, answer_ready >>

oa_synthesize(self) == /\ pc[self] = "oa_synthesize"
                       /\ TRUE
                       /\ pc' = [pc EXCEPT ![self] = "oa_verify"]
                       /\ UNCHANGED << verified, answer_ready >>

oa_verify(self) == /\ pc[self] = "oa_verify"
                   /\ \/ /\ pc' = [pc EXCEPT ![self] = "oa_verify_pass"]
                      \/ /\ pc' = [pc EXCEPT ![self] = "oa_verify_fail"]
                   /\ UNCHANGED << verified, answer_ready >>

oa_verify_pass(self) == /\ pc[self] = "oa_verify_pass"
                        /\ TRUE
                        /\ TRUE
                        /\ TRUE
                        /\ verified' = [verified EXCEPT ![self] = "pass"]
                        /\ pc' = [pc EXCEPT ![self] = "oa_decide"]
                        /\ UNCHANGED answer_ready

oa_verify_fail(self) == /\ pc[self] = "oa_verify_fail"
                        /\ \/ /\ pc' = [pc EXCEPT ![self] = "oa_verify_violation"]
                           \/ /\ pc' = [pc EXCEPT ![self] = "oa_verify_insufficient"]
                        /\ UNCHANGED << verified, answer_ready >>

oa_verify_violation(self) == /\ pc[self] = "oa_verify_violation"
                             /\ TRUE
                             /\ verified' = [verified EXCEPT ![self] = "violation"]
                             /\ pc' = [pc EXCEPT ![self] = "oa_decide"]
                             /\ UNCHANGED answer_ready

oa_verify_insufficient(self) == /\ pc[self] = "oa_verify_insufficient"
                                /\ TRUE
                                /\ verified' = [verified EXCEPT ![self] = "insufficient"]
                                /\ pc' = [pc EXCEPT ![self] = "oa_decide"]
                                /\ UNCHANGED answer_ready

oa_decide(self) == /\ pc[self] = "oa_decide"
                   /\ IF verified[self] = "pass"
                         THEN /\ pc' = [pc EXCEPT ![self] = "oa_emit_answer"]
                         ELSE /\ IF verified[self] = "violation"
                                    THEN /\ pc' = [pc EXCEPT ![self] = "oa_synthesize"]
                                    ELSE /\ pc' = [pc EXCEPT ![self] = "oa_emit_insufficient"]
                   /\ UNCHANGED << verified, answer_ready >>

oa_emit_answer(self) == /\ pc[self] = "oa_emit_answer"
                        /\ TRUE
                        /\ answer_ready' = [answer_ready EXCEPT ![self] = TRUE]
                        /\ pc' = [pc EXCEPT ![self] = "occupancy_answer_agent_done"]
                        /\ UNCHANGED verified

oa_emit_insufficient(self) == /\ pc[self] = "oa_emit_insufficient"
                              /\ TRUE
                              /\ answer_ready' = [answer_ready EXCEPT ![self] = TRUE]
                              /\ pc' = [pc EXCEPT ![self] = "occupancy_answer_agent_done"]
                              /\ UNCHANGED verified

occupancy_answer_agent_done(self) == /\ pc[self] = "occupancy_answer_agent_done"
                                     /\ TRUE
                                     /\ pc' = [pc EXCEPT ![self] = "Done"]
                                     /\ UNCHANGED << verified, answer_ready >>

occupancy_answer_agent_proc(self) == oa_gather_camera_occupancy(self)
                                        \/ oa_gather_entry_events(self)
                                        \/ oa_gather_exit_events(self)
                                        \/ oa_gather_anonymous_tracks(self)
                                        \/ oa_gather_motion_events(self)
                                        \/ oa_gather_camera_coverage(self)
                                        \/ oa_synthesize(self)
                                        \/ oa_verify(self)
                                        \/ oa_verify_pass(self)
                                        \/ oa_verify_fail(self)
                                        \/ oa_verify_violation(self)
                                        \/ oa_verify_insufficient(self)
                                        \/ oa_decide(self)
                                        \/ oa_emit_answer(self)
                                        \/ oa_emit_insufficient(self)
                                        \/ occupancy_answer_agent_done(self)

(* Allow infinite stuttering to prevent deadlock on termination. *)
Terminating == /\ \A self \in ProcSet: pc[self] = "Done"
               /\ UNCHANGED vars

Next == (\E self \in {Occupancy_answer_agent}: occupancy_answer_agent_proc(self))
           \/ Terminating

Spec == /\ Init /\ [][Next]_vars
        /\ \A self \in {Occupancy_answer_agent} : WF_vars(occupancy_answer_agent_proc(self))

Termination == <>(\A self \in ProcSet: pc[self] = "Done")

\* END TRANSLATION 

AllDone == \A p \in {Occupancy_answer_agent}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {Occupancy_answer_agent}: pc[p] \in STRING

====
