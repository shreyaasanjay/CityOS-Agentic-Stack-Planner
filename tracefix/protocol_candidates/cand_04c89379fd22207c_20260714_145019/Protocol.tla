---- MODULE Protocol ----
EXTENDS Integers, Sequences, TLC

CONSTANTS North_approach, East_approach, South_approach, West_approach, Emergency_detector, Pedestrian_crossing_agent, Signal_controller

\* traffic_signal_coordination variant: four_way_emergency_pedestrian
(* --algorithm Protocol {
variables
  north_approach_to_signal_controller = <<>>; \* north_approach -> signal_controller, labels: ['status_report', 'timeout', 'failure_notice']
  east_approach_to_signal_controller = <<>>; \* east_approach -> signal_controller, labels: ['status_report', 'timeout', 'failure_notice']
  south_approach_to_signal_controller = <<>>; \* south_approach -> signal_controller, labels: ['status_report', 'timeout', 'failure_notice']
  west_approach_to_signal_controller = <<>>; \* west_approach -> signal_controller, labels: ['status_report', 'timeout', 'failure_notice']
  signal_controller_to_north_approach = <<>>; \* signal_controller -> north_approach, labels: ['grant_green', 'set_red', 'all_red']
  signal_controller_to_east_approach = <<>>; \* signal_controller -> east_approach, labels: ['grant_green', 'set_red', 'all_red']
  signal_controller_to_south_approach = <<>>; \* signal_controller -> south_approach, labels: ['grant_green', 'set_red', 'all_red']
  signal_controller_to_west_approach = <<>>; \* signal_controller -> west_approach, labels: ['grant_green', 'set_red', 'all_red']
  emergency_detector_to_signal_controller = <<>>; \* emergency_detector -> signal_controller, labels: ['emergency_detected', 'emergency_cleared']
  pedestrian_crossing_agent_to_signal_controller = <<>>; \* pedestrian_crossing_agent -> signal_controller, labels: ['pedestrian_request', 'pedestrian_clear']
  signal_controller_to_pedestrian_crossing_agent = <<>>; \* signal_controller -> pedestrian_crossing_agent, labels: ['crossing_grant', 'crossing_hold']
  intersection_green_lock = "FREE";
  emergency_override_lock = "FREE";
  pedestrian_phase_lock = "FREE";
  north_green = FALSE;
  east_green = FALSE;
  south_green = FALSE;
  west_green = FALSE;

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

fair process (north_approach_proc \in {North_approach})
variables msg = "";
{
  north_approach_request:
    send(north_approach_to_signal_controller, "status_report");
  north_approach_await_command:
    receive(signal_controller_to_north_approach, msg);
  north_approach_done:
    skip;
}

fair process (east_approach_proc \in {East_approach})
variables msg = "";
{
  east_approach_request:
    send(east_approach_to_signal_controller, "status_report");
  east_approach_await_command:
    receive(signal_controller_to_east_approach, msg);
  east_approach_done:
    skip;
}

fair process (south_approach_proc \in {South_approach})
variables msg = "";
{
  south_approach_request:
    send(south_approach_to_signal_controller, "status_report");
  south_approach_await_command:
    receive(signal_controller_to_south_approach, msg);
  south_approach_done:
    skip;
}

fair process (west_approach_proc \in {West_approach})
variables msg = "";
{
  west_approach_request:
    send(west_approach_to_signal_controller, "status_report");
  west_approach_await_command:
    receive(signal_controller_to_west_approach, msg);
  west_approach_done:
    skip;
}

fair process (emergency_detector_proc \in {Emergency_detector})
{
  emergency_detector_detect:
    send(emergency_detector_to_signal_controller, "emergency_detected");
  emergency_detector_done:
    skip;
}

fair process (pedestrian_crossing_agent_proc \in {Pedestrian_crossing_agent})
variables msg = "";
{
  pedestrian_crossing_agent_request:
    send(pedestrian_crossing_agent_to_signal_controller, "pedestrian_request");
  pedestrian_crossing_agent_await_command:
    receive(signal_controller_to_pedestrian_crossing_agent, msg);
  pedestrian_crossing_agent_done:
    skip;
}

fair process (signal_controller_proc \in {Signal_controller})
variables msg = "";
{
  signal_controller_receive_1:
    receive(north_approach_to_signal_controller, msg);
  signal_controller_receive_2:
    receive(east_approach_to_signal_controller, msg);
  signal_controller_receive_3:
    receive(south_approach_to_signal_controller, msg);
  signal_controller_receive_4:
    receive(west_approach_to_signal_controller, msg);
  signal_controller_receive_emergency:
    receive(emergency_detector_to_signal_controller, msg);
  signal_controller_receive_pedestrian:
    receive(pedestrian_crossing_agent_to_signal_controller, msg);
  signal_controller_acquire_intersection:
    acquire_lock(intersection_green_lock);
  signal_controller_safe_phase:
    north_green := TRUE;
    east_green := FALSE;
    south_green := FALSE;
    west_green := FALSE;
  signal_controller_transition_all_red:
    north_green := FALSE;
    east_green := FALSE;
    south_green := FALSE;
    west_green := FALSE;
  signal_controller_release_intersection:
    release_lock(intersection_green_lock);
  signal_controller_acquire_emergency_override:
    acquire_lock(emergency_override_lock);
  signal_controller_emergency_all_red:
    north_green := FALSE;
    east_green := FALSE;
    south_green := FALSE;
    west_green := FALSE;
  signal_controller_emergency_priority:
    north_green := TRUE; \* bounded emergency priority phase
  signal_controller_release_emergency_override:
    release_lock(emergency_override_lock);
  signal_controller_acquire_pedestrian_phase:
    acquire_lock(pedestrian_phase_lock);
  signal_controller_pedestrian_all_red:
    north_green := FALSE;
    east_green := FALSE;
    south_green := FALSE;
    west_green := FALSE;
  signal_controller_pedestrian_command:
    send(signal_controller_to_pedestrian_crossing_agent, "crossing_grant");
  signal_controller_release_pedestrian_phase:
    release_lock(pedestrian_phase_lock);
  signal_controller_failure_all_red:
    north_green := FALSE;
    east_green := FALSE;
    south_green := FALSE;
    west_green := FALSE;
  signal_controller_command_1:
    send(signal_controller_to_north_approach, "all_red");
  signal_controller_command_2:
    send(signal_controller_to_east_approach, "all_red");
  signal_controller_command_3:
    send(signal_controller_to_south_approach, "all_red");
  signal_controller_command_4:
    send(signal_controller_to_west_approach, "all_red");
  signal_controller_done:
    skip;
}

} *)

AllDone == \A p \in {North_approach, East_approach, South_approach, West_approach, Emergency_detector, Pedestrian_crossing_agent, Signal_controller}: pc[p] = "Done"

TypeInvariant ==
  /\ \A p \in {North_approach, East_approach, South_approach, West_approach, Emergency_detector, Pedestrian_crossing_agent, Signal_controller}: pc[p] \in STRING
  /\ intersection_green_lock \in {North_approach, East_approach, South_approach, West_approach, Emergency_detector, Pedestrian_crossing_agent, Signal_controller, "FREE"}
  /\ emergency_override_lock \in {North_approach, East_approach, South_approach, West_approach, Emergency_detector, Pedestrian_crossing_agent, Signal_controller, "FREE"}
  /\ pedestrian_phase_lock \in {North_approach, East_approach, South_approach, West_approach, Emergency_detector, Pedestrian_crossing_agent, Signal_controller, "FREE"}
  /\ north_green \in BOOLEAN
  /\ east_green \in BOOLEAN
  /\ south_green \in BOOLEAN
  /\ west_green \in BOOLEAN
  /\ north_approach_to_signal_controller \in Seq(STRING)
  /\ east_approach_to_signal_controller \in Seq(STRING)
  /\ south_approach_to_signal_controller \in Seq(STRING)
  /\ west_approach_to_signal_controller \in Seq(STRING)
  /\ signal_controller_to_north_approach \in Seq(STRING)
  /\ signal_controller_to_east_approach \in Seq(STRING)
  /\ signal_controller_to_south_approach \in Seq(STRING)
  /\ signal_controller_to_west_approach \in Seq(STRING)
  /\ emergency_detector_to_signal_controller \in Seq(STRING)
  /\ pedestrian_crossing_agent_to_signal_controller \in Seq(STRING)
  /\ signal_controller_to_pedestrian_crossing_agent \in Seq(STRING)

NoConflictingGreens ==
  ~(north_green /\ east_green) /\ ~(north_green /\ south_green) /\ ~(north_green /\ west_green) /\ ~(east_green /\ south_green) /\ ~(east_green /\ west_green) /\ ~(south_green /\ west_green)

AllRedOnCompletion ==
  AllDone =>
    ~north_green /\ ~east_green /\ ~south_green /\ ~west_green

NoOrphanLocks ==
  AllDone => (intersection_green_lock = "FREE" /\ emergency_override_lock = "FREE" /\ pedestrian_phase_lock = "FREE")

ChannelsDrained ==
  AllDone => (Len(north_approach_to_signal_controller) = 0 /\ Len(east_approach_to_signal_controller) = 0 /\ Len(south_approach_to_signal_controller) = 0 /\ Len(west_approach_to_signal_controller) = 0 /\ Len(signal_controller_to_north_approach) = 0 /\ Len(signal_controller_to_east_approach) = 0 /\ Len(signal_controller_to_south_approach) = 0 /\ Len(signal_controller_to_west_approach) = 0 /\ Len(emergency_detector_to_signal_controller) = 0 /\ Len(pedestrian_crossing_agent_to_signal_controller) = 0 /\ Len(signal_controller_to_pedestrian_crossing_agent) = 0)

ChannelBound ==
  Len(north_approach_to_signal_controller) <= 3 /\ Len(east_approach_to_signal_controller) <= 3 /\ Len(south_approach_to_signal_controller) <= 3 /\ Len(west_approach_to_signal_controller) <= 3 /\ Len(signal_controller_to_north_approach) <= 3 /\ Len(signal_controller_to_east_approach) <= 3 /\ Len(signal_controller_to_south_approach) <= 3 /\ Len(signal_controller_to_west_approach) <= 3 /\ Len(emergency_detector_to_signal_controller) <= 3 /\ Len(pedestrian_crossing_agent_to_signal_controller) <= 3 /\ Len(signal_controller_to_pedestrian_crossing_agent) <= 3

====
