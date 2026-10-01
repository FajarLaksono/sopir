# SUMO Research Reference

Research notes on [Eclipse SUMO](https://eclipse.dev/sumo/) and the
[TraCI](https://eclipse.dev/sumo/docs/TraCI/index.html) control interface,
scoped to what this project can observe, what it currently observes, and the
gap between the two.

**Version researched:** SUMO 1.27.1 (matches `Dockerfile.worker` and
`requirements.txt`).

**Sources:** official SUMO documentation at `eclipse.dev/sumo/docs` and
`sumo.dlr.de/docs`.

---

## 1. What SUMO Is

SUMO (Simulation of Urban MObility) is an open-source, microscopic traffic
simulator. Microscopic means every individual vehicle is modelled explicitly
with its own position, speed, acceleration, lane, and route, and driver models
determine how it interacts with other vehicles and with infrastructure such as
traffic lights.

It ships as:

| Component | Purpose |
|-----------|---------|
| `sumo` | The core simulator, headless-capable |
| `netconvert` | Builds `.net.xml` road networks from OpenStreetMap or plain XML |
| `netgenerate` | Generates synthetic networks (grid, spider, random) |
| `duarouter` | Computes fastest routes between points |
| `marouter` | Macro-traffic assignment (uses the same vehicle model) |
| `sumo-gui` | Visual editor and viewer, optional |

**Important framing for this project:** SUMO is a *traffic* simulator, not an
autonomous-driving stack. It models human drivers via behavioural models
(Krauss, Gipps, IDM, LC2013, SL2015), not ADAS perception or planning. Using it
for validation means you are testing *your* controller against a population of
scripted human drivers. SUMO gives you a controllable, reproducible,
high-throughput world to exercise that controller in. It does not give you a
sensor model, an occupancy grid, or a perception stack.

---

## 2. Integration Models: TraCI vs libsumo

SUMO exposes three integration paths.

### 2.1 TraCI (socket)

SUMO runs as a server process; a client opens a TCP connection and issues
commands. Python ships `traci`, installable with `pip install traci`.

```
client ──TCP──> sumo server process
        <── status + results
```

- Language-agnostic: Java, C++, .NET, Matlab bindings all exist.
- SUMO runs as a separate OS process.
- **Socket serialisation overhead slows simulation.** SUMO's docs are explicit
  that using TraCI reduces simulation speed, and that `libsumo` should be
  preferred when performance matters.

### 2.2 libsumo (embedded)

SUMO compiled as a library, linked directly into the client. Python: `pip
install libsumo`. The API surface is deliberately **identical** to `traci`,
including function signatures.

- No socket, no subprocess. Much faster.
- No multi-client, no `sumo-gui` coupling.
- C++ / Java / Python (SWIG) bindings.

This is the recommended path for throughput. Because signatures match, migration
is largely a matter of swapping the import and adjusting startup.

### 2.3 libtraci (SWIG client)

SWIG-generated C++ client library, fully Libsumo-compatible, supports multiple
clients and running alongside `sumo-gui`. For Python the docs recommend plain
`traci` over `libtraci`.

### 2.4 Recommendation for this project

Stay on `traci` for the MVP. The current workload is 4 scenarios × 100 simulated
seconds at 0.1 s steps = 1000 steps total, which is trivial. Revisit `libsumo`
only when the scenario count or step rate grows by orders of magnitude. The
value of a drop-in `libsumo` switch is that it becomes a one-line change, so
there is no need to architect around TraCI's limits.

---

## 3. Protocol Basics

SUMO's TraCI protocol is a TCP message container with a size header followed by
a list of length-prefixed, identifier-prefixed commands. Each command is
acknowledged with a status response.

Elementary data types on the wire:

| Type | Size | Meaning |
|------|------|---------|
| `ubyte` | 8 bit | 0–255 |
| `byte` | 8 bit | −128–127 |
| `integer` | 32 bit | signed; bitsets use this |
| `double` | 64 bit | IEEE754 |
| `string` | variable | 32-bit length + ASCII |
| `stringList` | variable | 32-bit count + strings |
| `compound` | variable | 32-bit component count + ordered components |

Status result codes:

| Code | Meaning |
|------|---------|
| `0x00` | Success |
| `0xFF` | Command failed |
| `0x01` | Not implemented (description string attached) |

### 3.1 Position Types

TraCI returns positions in several representations, chosen by the client:

| Type | Description |
|------|-------------|
| 2D position | `(x, y)` in metres in the local coordinate frame |
| 3D position | `(x, y, z)` |
| Geo position | `(lon, lat)` |
| Road map position | `(edgeId, pos, laneId)`, lanes numbered right-to-left from 0 |

`traci.simulation.convert2D` / `convert3D` / `convertGeo` / `convertRoad`
convert between them. **Conversion is lossy in one direction**: going from
coordinates to a road position yields the *closest matching* position, not an
exact one. Relevant if you ever snap detected objects onto lanes.

### 3.2 Control Commands

| Command | ID | Python | Notes |
|---------|-----|--------|-------|
| Load | `0x01` | `traci.load` | Reload sim with new options |
| Simulation step | `0x02` | `traci.simulationStep` | `targetTime=0` steps once; sim advances when all clients have stepped |
| SetOrder | `0x03` | `traci.setOrder` | Required first call in multi-client setups |
| Close | `0x7f` | `traci.close` | Closes connection, stops sim, shuts down |

**`--end` is ignored under TraCI.** The simulation ends when all clients issue
`close`, not when the configured end time is reached. To detect exhaustion of
input, poll `traci.simulation.getMinExpectedNumber()` and check for `0`. This is
precisely the loop condition in `simulation/worker.py`.

`getVersion` returns `(api_version, "SUMO v1_27_1")` and is the correct way to
feature-detect.

---

## 4. Retrieval Modes

Three ways to get data out of SUMO, in ascending order of efficiency.

### 4.1 Polling (slowest)

Call a getter per vehicle per variable per step. For a run with V vehicles, K
variables, and T steps that is `V × K × T` round trips. Every round trip
serialises the whole simulation.

### 4.2 Object Variable Subscriptions

Register interest in a variable for an object once; SUMO then returns the
value after every step.

```python
traci.vehicle.subscribe(vid, [tc.VAR_SPEED, tc.VAR_POSITION])
while ...:
    traci.simulationStep()
    values = traci.vehicle.getAllSubscriptionResults()
```

- Handled per module; you retrieve that module's subscription results.
- Variable ids come from `traci/constants.py`.
- **Values are always from the last step.** Older values cannot be retrieved.
- Before SUMO 1.18.0, `traci.simulationStep()` returned subscription results as a
  return value. From 1.18.0 onward it returns `None`; use
  `traci.simulationStepLegacy()` for the old behaviour. We are on 1.27.1, so
  this applies to us.
- Docs note subscriptions can even be slower under libsumo, because the
  subscription filtering has to be emulated client-side.

### 4.3 Context Subscriptions

Subscribe to variables of all objects *near a reference object*, within a radius.
This is the single most relevant feature for an ego-centric ADAS workload.

```python
traci.junction.subscribeContext(
    junction_id, tc.CMD_GET_VEHICLE_VARIABLE, 42.0, [tc.VAR_SPEED, tc.VAR_POSITION]
)
```

It is possible to subscribe to variables of vehicles surrounding another
**vehicle**:

```python
traci.vehicle.subscribeContext(ego_id, tc.CMD_GET_VEHICLE_VARIABLE, 100.0, [tc.VAR_SPEED])
```

For vehicle-to-vehicle context subscriptions the server side supports extra
filters, applied by calling `addSubscriptionFilter<FILTER_ID>()` immediately
after `subscribeContext()`. This lets you ask for "the 3 nearest vehicles ahead"
rather than everything inside a disc.

Same last-step-only caveat applies.

### 4.4 StepListener

To hook code that must run after every step, subclass `traci.StepListener`.
SUMO calls it automatically after each `simulationStep()`, so the hook cannot be
forgotten or ordered incorrectly.

### 4.5 Multiple Simulations from One Script

`traci.start(cmd, label="a")` accepts a `label`, and `traci.switch("a")`
reconnects. One script can drive several SUMO instances.

### 4.6 Trace Logging

`traci.start(cmd, traceFile=path)` logs every TraCI command sent to SUMO,
producing a standalone re-runnable script. `traceGetters=False` logs only state-
changing calls; `traceGetters="print"` makes getters print themselves and their
results. Invaluable for producing a minimal reproduction from a CI failure.

---

## 5. Telemetry Inventory

### 5.1 Vehicle Domain — `traci.vehicle` (command `0xa4`)

The largest domain, and the one that matters most here.

#### Currently used by this project

| Variable | ID | Type | Python | Notes |
|----------|-----|------|--------|-------|
| id list | `0x00` | stringList | `getIDList` | All running vehicle ids |
| position | `0x42` | position | `getPosition` | Front bumper centre, `[−2³⁰, −2³⁰]` on error |
| speed | `0x40` | double | `getSpeed` | Longitudinal, m/s |
| angle | `0x54` | double | `getAngle` | Heading in degrees, 0–360 |
| lane id | `0x51` | string | `getLaneID` | `""` on error |

#### High-value additions for ADAS validation

| Variable | ID | Type | Python | Why it matters |
|----------|-----|------|--------|----------------|
| acceleration | `0x72` | double | `getAcceleration` | Real longitudinal state, not a position finite-difference |
| lateral speed | `0x32` | double | `getLateralSpeed` | Quantifies lane-change aggressiveness |
| lane position | `0x56` | double | `getLanePosition` | Metres along lane; needed for gap-in-numbers, not just gap-in-metres |
| leader | `0x68` | compound | `getLeader(vid, dist)` | `(leader_id, distance)`. Ground-truth lead vehicle |
| signal states | `0x5b` | int | `getSignals` | Bitmask; see §5.2 |
| lateral lane position | `0xb8` | double | `getLateralLanePosition` | Metres from lane centre. Exact lane-keeping deviation |
| lane index | `0x52` | int | `getLaneIndex` | Which lane of a multi-lane edge |
| speed limit | `0xd7` | double | `getSpeedLimit` | Makes speed violations network-derived instead of hardcoded |
| allowed speed | `0xb7` | double | `getAllowedSpeed` | What the vehicle *may* do here, accounting for its type |
| vehicle class | `0x49` | string | `getVehicleClass` | Distinguishes car / truck / bus / motorcycle |
| type id | `0x4f` | string | `getTypeID` | Distinguishes ego from background traffic |
| best lanes | `0xb2` | complex | `getBestLanes` | Per-lane continuations plus lane-change desirability. Highest-fidelity prediction available |
| lane change state | — | compound | `getLaneChangeState` | Intent bitset + reason bits. See §5.3 |
| route | `0x57` | stringList | `getRoute` | Planned edges |
| route index | `0x69` | int | `getRouteIndex` | Progress along route |
| road id | `0x50` | string | `getRoadID` | Current edge |
| length | `0x4d` | double | `getLength` | Vehicle footprint, needed for real gap rather than centre distance |
| width | `0x4c` | double | `getWidth` | |
| height | `0xbc` | double | `getHeight` | |
| waiting time | `0x7a` | double | `getWaitingTime` | Congestion delay |
| time loss | `0x7c` | double | `getTimeLoss` | Delay vs free-flow reference |
| speed factor | `0x9e` | double | `getSpeedFactor` | This vehicle's aggression multiplier |
| speed without TraCI | — | double | `getSpeedWithoutTraCI` | Speed absent any `setSpeed` override; lets you measure your own controller's effect |
| drivetime | `0x66` | double | `getDriveTime` | Time spent moving |
| changing lane | `0x5c` | double | `getChangingLane` | Lateral offset during a lane change |
| next stops | `0x73` | compound | `getNextStops` | **Not subscribable** |

IDs for `getSpeedWithoutTraCI` and a few rarely used getters are omitted where
the published tables were not unambiguous; look them up in `traci/constants.py`
before subscribing.

#### Emissions and energy

All are mg/s *rates*, so integrate over step length to get a quantity.
Requires an emission model (`--emission-model`).

| Variable | Python |
|----------|--------|
| CO2 emissions | `getCO2Emission` |
| CO emissions | `getCOEmission` |
| HC emissions | `getHCEmission` |
| NOx emissions | `getNOxEmission` |
| PMx emissions | `getPMxEmission` |
| noise emissions | `getNoiseEmission` |
| fuel consumption | `getFuelConsumption` |
| electricity consumption | `getElectricityConsumption` |

Consult `traci/constants.py` for the current IDs; they shift between SUMO
releases and several overlap with non-emission variables in the same numeric
range.

**Error value is `−2³⁰` for every one of these.** Unguarded arithmetic on a
missing value produces large negative garbage rather than an exception. Any
metrics function consuming these must filter the sentinel.

### 5.2 Vehicle Signal Bitmask

`getSignals` returns an integer with one bit per signal.

| Bit | Name | Computed by SUMO each step? |
|-----|------|------------------------------|
| 0 | `VEH_SIGNAL_BLINKER_RIGHT` | Yes |
| 1 | `VEH_SIGNAL_BLINKER_LEFT` | Yes |
| 2 | `VEH_SIGNAL_BLINKER_EMERGENCY` | No |
| 3 | `VEH_SIGNAL_BRAKELIGHT` | Yes |
| 4 | `VEH_SIGNAL_FRONTLIGHT` | No |
| 5 | `VEH_SIGNAL_FOGLIGHT` | No |
| 6 | `VEH_SIGNAL_HIGHBEAM` | No |
| 7 | `VEH_SIGNAL_BACKDRIVE` | No |
| 8 | `VEH_SIGNAL_WIPER` | No |
| 9 | `VEH_SIGNAL_DOOR_OPEN_LEFT` | No |
| 10 | `VEH_SIGNAL_DOOR_OPEN_RIGHT` | No |
| 11 | `VEH_SIGNAL_EMERGENCY_BLUE` | No |
| 12 | `VEH_SIGNAL_EMERGENCY_RED` | No |
| 13 | `VEH_SIGNAL_EMERGENCY_YELLOW` | No |

SUMO computes the blinkers when the vehicle is about to turn at an intersection
(7 s before arrival), when a continuous lane-change model is active, when a
desired lane change is blocked by neighbours, when preparing to stop for
parking, or when stopped on a non-rightmost lane (emergency blinkers).

`BRAKELIGHT` is set when the vehicle is standing still (but not fully stopped)
or decelerates beyond a threshold.

Note these are derived from SUMO's *own* lane-change and intersection logic.
They are the reference behaviour to compare an ADAS against, not an independent
ground truth.

### 5.3 Lane Change State Bitset

`getLaneChangeState` returns two integers: the state from SUMO's lane-change
model, and the state after TraCI requests were folded in. Both use this bitset:

| Bit | Meaning |
|-----|---------|
| `2^0` | stay |
| `2^1` | left |
| `2^2` | right |
| `2^3` | strategic |
| `2^4` | cooperative |
| `2^5` | speedGain |
| `2^6` | keepRight |
| `2^7` | TraCI |
| `2^8` | urgent |
| `2^9` | blocked by left leader |
| `2^10` | blocked by left follower |
| `2^11` | blocked by right leader |
| `2^12` | blocked by right follower |
| `2^13` | overlapping |
| `2^14` | insufficient space |
| `2^15` | sublane |
| `2^28` | insufficient speed |
| `2^30` | undetermined |

This is the most safety-relevant item in the whole vehicle domain: it exposes
*why* a lane change did or did not happen, not just that it happened.

### 5.4 Vehicle Type Domain — `traci.vehicletype`

Static parameters, retrievable and subscribable: max speed, accel, decel, length,
width, height, min gap, sigma (driver imperfection), maxSpeedLat, minSpeedLat,
and the driver's `speedFactor`. Useful for classifying a mixed fleet rather than
hardcoding assumptions.

### 5.5 Person Domain — `traci.person` (command `0xae`)

**Not collected at all today.** The project has a `pedestrian_crossing`
scenario but the worker loop only walks `traci.vehicle.getIDList()`.

| Variable | ID | Type | Python |
|----------|-----|------|--------|
| id list | `0x00` | stringList | `getIDList` |
| position | `0x42` | position | `getPosition` |
| speed | `0x40` | double | `getSpeed` |
| angle | `0x54` | double | `getAngle` |
| road id | `0x50` | string | `getRoadID` |
| lane id | `0x51` | string | `getLaneID` |
| edge position | `0x56` | double | `getLanePosition` |
| next edge | `0xc1` | string | `getNextEdge` |
| remaining stages | `0xc2` | int | `getRemainingStages` |
| stage | `0xc0` | TraCIStage | `getStage` |
| edges | `0x54` | stringList | `getEdges` |
| waiting time | `0x7a` | double | `getWaitingTime` |
| length | `0x77` | double | `getLength` |
| apparent position | `0x55` | position | `getApparentPosition` |

`splitTaxiReservation` (`0xc7`) is retrieval-only but mutates state; it is not
subscribable.

### 5.6 Simulation Domain — `traci.simulation` (command `0xab`)

| Variable | ID | Type | Python | Notes |
|----------|-----|------|--------|-------|
| current time | `0x66` | double | `getTime` | Seconds |
| delta T | `0x7b` | double | `getDeltaT` | Step length in seconds |
| minimum expected number | `0x87` | int | `getMinExpectedNumber` | Exhaustion check |
| departed vehicles | `0x7d` | stringList | `getDepartedIDList` | Joined this step |
| arrived vehicles | `0x7a` | stringList | `getArrivedIDList` | Left this step |
| loaded | `0x6b` | int | `getLoadedNumber` | |
| teleports started | `0x75` | int | `getStartingTeleportNumber` | Jam teleports. A reliability signal |
| teleports ended | `0x76` | int | `getEndingTeleportNumber` | |
| scale | `0x8e` | double | `getScale` | Traffic scaling factor |
| option | `0x3f` | string | `getOption` | Read back a global SUMO option |
| parameter | `0x7e` | string | `getParameter` | Generic parameter retrieval |

Extended (with extra parameters, **not subscribable**):

| Message | ID | Python | Notes |
|---------|-----|--------|-------|
| position conversion | `0x82` | `convert2D/3D/Geo/Road` | |
| distance | `0x83` | `getDistanceRoad`, `getDistance2D` | Choose road vs air distance |
| find route | `0x86` | `findRoute` | Fastest route from edge→edge for a vehicle type. Returns `stageType, line, destStop, edges, travelTime, cost` |
| find intermodal route | `0x87` | `findIntermodalRoute` | Includes walking speed, walk factor, car/public/bike modes |
| **get collisions** | `0x23` | `getCollisions` | All collision events in the **last step** |

**Collision detection is disabled by default** (`--collision.action none`). Our
scenario `.sumocfg` templates set `collision.action=warn`, so SUMO computes and
would report collisions, but the worker never calls `getCollisions()`.

### 5.7 Generic Parameter Retrieval — `getParameter`

`getParameter(domain, key, objID)` reaches parameters not in the standard
tables. Two families matter here.

#### Statistics — `stats.*`

Retrieved with `objID=""`:

```
stats.vehicles.loaded      stats.vehicles.inserted    stats.vehicles.running
stats.vehicles.waiting     stats.teleports.total      stats.teleports.jam
stats.teleports.yield      stats.teleports.wrongLane  stats.safety.collisions
stats.safety.emergencyStops  stats.safety.emergencyBraking
stats.persons.loaded       stats.persons.running      stats.persons.jammed
stats.personTeleports.total  stats.personTeleports.abortWait
stats.personTeleports.wrongDest
```

`stats.safety.emergencyBraking` and `stats.safety.emergencyStops` are directly
usable safety KPIs without any custom metric code.

#### Trip statistics — `device.tripinfo.*`

Requires attaching the `tripinfo` device. Aggregate by mode: `vehicleTripStatistics`,
`bikeTripStatistics`, `pedestrianStatistics`, `rideStatistics`,
`transportStatistics`, plus generic `device.tripinfo.*`. Each exposes `count`,
`number`, `routeLength`, `speed`, `duration`, `waitingTime`, `timeLoss`,
`departDelay`, `departDelayWaiting`, `totalTravelTime`, `totalDepartDelay`.

This gives network-level KPIs (average trip delay, total time loss) that our
per-run metrics cannot produce.

### 5.8 Infrastructure Domains

| Domain | Module | Key variables |
|--------|--------|---------------|
| Traffic lights | `traci.trafficlight` | `getRedYellowGreenState` (`0x20`, `rRgGyYoO` where lowercase means decelerating), `getPhase` (`0x28`), `getProgram` (`0x29`), `getControlledLanes` (`0x26`), `getControlledLinks` (`0x27`), `getNextSwitch` (`0x2d`, **absolute** time — subtract current time for relative), `getCompleteRedYellowGreenDefinition` (`0x2b`) |
| Induction loops (e1) | `traci.inductionloop` | `getLastStepVehicleNumber` (`0x10`), `getLastStepVehicleIDs` (`0x12`), `getVehicleData` (`0x17`), `getIntervalVehicleNumber` (`0x25`), `getLastIntervalVehicleNumber` (`0x29`) |
| Lane area detectors (e2) | `traci.lanearea` | Requires detectors declared in an additional file; `period`/`file` attributes ignored under TraCI |
| Multi-entry/multi-exit | `traci.multientryexit` | |
| Edges / lanes / junctions | `traci.edge`, `traci.lane`, `traci.junction` | Shape, length, speed limit, width, allowed lanes, connections, shape3D |
| POI / polygon | `traci.poi`, `traci.polygon` | Regions for map-based triggers |
| Bus stops | `traci.busstop` | |
| Charging stations | `traci.chargingstation` | `getParameter("chargingstation.totalEnergyCharged")` |
| Parking areas | `traci.parkingarea` | |
| Route probes | `traci.routeprobe` | Travel-time measurement along a corridor |

Also `traci.vehicle.setSignals`, `setSpeed`, `changeLane`, `setLaneChangeMode`,
`setRoute`, `setStop`, `moveTo`, `setSpeedWithoutTraci` for *closed-loop*
control, which is how you would attach a real controller to SUMO.

### 5.9 Output-File Alternative to TraCI

SUMO can write floating-car data directly, without TraCI:

```
--fcd-output trajectories.xml
--tripinfo-output tripinfo.xml
--summary-output summary.xml
--statistic-output stats.xml
```

`--fcd-output` includes vehicle signals, so blinkers and brake lights are
available offline. If the use case is *collect and analyse later* rather than
*control in the loop*, file output is faster and simpler than TraCI polling. It
loses the closed-loop capability.

---

## 6. Gap Analysis: What We Collect vs What Exists

### 6.1 Coverage

| Signal | TraCI call | Collected | Priority |
|--------|-----------|-----------|----------|
| Position | `getPosition` | Yes | — |
| Speed | `getSpeed` | Yes | — |
| Angle | `getAngle` | Yes | — |
| Lane id | `getLaneID` | Yes | — |
| Acceleration | `getAcceleration` | **No** | High |
| Leader + gap | `getLeader` | **No** | High |
| Signals | `getSignals` | **No** | High |
| Lane position | `getLanePosition` | **No** | Medium |
| Lateral lane position | `getLateralLanePosition` | **No** | Medium |
| Speed limit | `getSpeedLimit` | **No** | Medium |
| Vehicle type / class | `getTypeID`, `getVehicleClass` | **No** | Medium |
| Length / width | `getLength`, `getWidth` | **No** | Medium |
| Lane change state | `getLaneChangeState` | **No** | High |
| Best lanes | `getBestLanes` | **No** | Medium |
| Waiting time / time loss | `getWaitingTime`, `getTimeLoss` | **No** | Low |
| Emissions | `getCO2Emission` etc. | **No** | Low |
| **Persons** | `traci.person.*` | **No** | High |
| **Collisions (SUMO-reported)** | `simulation.getCollisions` | **No** | High |
| **Safety stats** | `getParameter("stats.safety.*")` | **No** | Medium |
| Traffic light phase | `traci.trafficlight.*` | **No** | Medium |

Three of the highest-value items are missing: persons, SUMO's own collision
report, and vehicle type. Each breaks something we currently cannot do.

### 6.2 Problems in What We Do Collect

These are correctness issues, not just gaps.

**Persons are invisible.** The worker iterates `traci.vehicle.getIDList()` only.
The `pedestrian_crossing` scenario defines a pedestrian, so that pedestrian is
never recorded. Any metric that reasons about vulnerable road users cannot work.
Pedestrian-ego interaction is unanalysable in the current design.

**There is no ego concept.** Nothing identifies which vehicle is the one under
test. Consequently `avg_speed` averages *all* vehicles, so a metric named after
ego behaviour does not measure ego behaviour. The `Metrics.avg_speed` column and
its documented meaning ("mean speed of ego vehicle") disagree. Adding
`getTypeID` and tagging an ego at scenario-generation time fixes this.

**Collision detection is a geometric proxy, not SUMO's answer.** `collision_count`
in `evaluation/metrics.py` counts vehicle pairs closer than 2.5 m. SUMO computes
real collision events from its own geometry model, already enabled via
`collision.action=warn`, and exposes them via `getCollisions()`. The proxy
double-counts (one pair colliding over many consecutive steps counts many times),
misses vehicles whose bodies overlap while centres are more than 2.5 m apart, and
is hardcoded rather than derived from vehicle dimensions.

**TTC uses scalar relative speed, which is wrong for the interesting cases.**
The implementation is `ttc = dist / abs(v1 - v2)`. This only holds for vehicles
approaching along a common axis. It breaks in exactly the scenarios that matter:

- Two vehicles crossing perpendicular paths at similar speeds have
  `abs(v1 - v2) ≈ 0`, so TTC → ∞ and is discarded. The `intersection` scenario is
  likely blind to this.
- Vehicles travelling in the same direction on parallel lanes at equal speed
  produce `v_rel = 0` and no TTC, even when they are 1 m apart laterally.
- Vehicles moving in *opposite* directions have large `abs(v1 - v2)`, which is
  accidentally closer to correct, but only because the axis happens to align.

Proper TTC needs vectors: closing rate along the line of sight,
`v_close = (Δr · Δv) / |Δr|`, then `TTC = −|Δr| / v_close` for `v_close < 0`.
The scalar form also ignores whether the pair is closing or separating, so
diverging vehicles can register a finite "TTC".

**Speed violations use a hardcoded 15 m/s, not the network limit.** Scenarios
have differing limits; a threshold tuned to one network is meaningless on
another. `getSpeedLimit` and `getAllowedSpeed` make this network-derived and
correctly per-vehicle-type. The plan document already specifies
`speed_limit * 1.1`.

**Lane deviations conflate legal and illegal.** `lane_deviations` counts every
`lane_id` change. Lane ids also change when a vehicle *turns at a junction*, so
the intersection and merge scenarios inflate this metric with perfectly correct
manoeuvres. The intended rule was "lane changes without signalling", and
`getSignals` provides exactly that evidence.

**No person, vehicle-type, or limit columns on `Telemetry`.** The schema records
only kinematics and one lane string, so none of the above can be computed
post-hoc without re-running.

**Metrics recomputation is quadratic.** `compute_metrics` loops over
`range(max_step + 1)` and, inside each iteration, filters the full telemetry list
by step. That is O(steps × N) with a scan per step, plus O(V²) for pairwise TTC
per step. It works at test scale and will not scale to a real run. Grouping by
step into a dict first removes the repeated scan.

**Batching by row count conflates vehicles and steps.** `TELEMETRY_BATCH_SIZE`
compares against `len(telemetry_batch)`, which counts rows, so with 4 vehicles
running a batch flushes every 25 steps, not every 100. Harmless, but the
configured meaning is not the implemented meaning.

---

## 7. Recommended Next Steps

Ordered by value per unit of effort.

### P0 — Correctness

| # | Change | Rationale |
|---|--------|-----------|
| 1 | Collect persons via `traci.person`, add person telemetry rows | Makes the pedestrian scenario meaningful |
| 2 | Tag ego via `getTypeID` at generation time | Makes `avg_speed` mean what it claims |
| 3 | Replace geometric collision proxy with `getCollisions()` | Authoritative, and already enabled in config |
| 4 | Rewrite TTC with vector closing rate | Fixes crossing-path and parallel-lane cases |
| 5 | Derive speed limit from `getSpeedLimit` | Removes hardcoded threshold |

### P1 — Signal Quality

| # | Change | Rationale |
|---|--------|-----------|
| 6 | Add `acceleration`, `lateral_speed`, `lane_position`, `lateral_lane_position` | Better dynamics, real lane-keeping deviation |
| 7 | Add `signals` column; make `lane_deviations` check for unsignalled changes | Turns a proxy into the specified rule |
| 8 | Add `vehicle_type`, `length`, `width` | Fleet classification, correct gap computation |
| 9 | Switch to object variable subscriptions | Removes `V × K × T` round trips |

### P2 — Scale

| # | Change | Rationale |
|---|--------|-----------|
| 10 | Vehicle-to-vehicle context subscription for ego + 3 nearest | Matches the documented intent, and scales with ego neighbourhood instead of network size |
| 11 | Group telemetry by step before metric computation | Removes the O(steps × N) scan |
| 12 | Move to libsumo | Same API signatures, one-line swap, removes socket overhead |
| 13 | Consider `--fcd-output` for offline batch analysis | Faster than TraCI when no closed loop is needed |

### P3 — Safety KPIs

| # | Change | Rationale |
|---|--------|-----------|
| 14 | Read `stats.safety.emergencyBraking` / `emergencyStops` | Real SUMO safety events, no custom logic |
| 15 | Attach `tripinfo` device for trip-level KPIs | Network delay and time-loss metrics |
| 16 | Read traffic light phase for intersection right-of-way | Enables red-light and junction-phase failures |

---

## 8. Design Notes for This Project

### 8.1 Ego-centric is the right frame

A validation platform for AD/ADAS should measure the ego against its
surroundings, not aggregate the whole network. The `vehicle` → context
subscription path (vehicle-to-vehicle context subscriptions, with server-side
filters) is designed for exactly this. Recording ego plus its 3 nearest
neighbours bounds telemetry volume independently of scenario size and matches the
stated intent in `AGENTS.md`.

### 8.2 Ground truth vs reference behaviour

SUMO's lane-change intent and blinker state come from its own behavioural models.
They are the *reference under test*, not independent truth. When reporting
"ego failed to signal", the honest framing is "ego diverged from SUMO's reference
signal convention". Worth being precise about in a portfolio, because an
interviewer will ask.

### 8.3 Reproducibility is a genuine strength

Scenarios are fully determined: fixed `.rou.xml` inputs, fixed step length,
scripted departure times, fixed driver parameters. SUMO itself is deterministic
for identical inputs, so a failure is reproducible by replaying the same
scenario. `traci.start(traceFile=...)` extends this to the control loop itself.
This is a real advantage over log-based validation of real vehicle data and
should be called out.

### 8.4 The honest limitation

SUMO gives no sensor model, no perception noise, no occlusion, no localisation
error. It is a perfect-observation environment with scripted human drivers.
Claims should stay at "controller behaviour under scripted traffic", not
"real-world validation".

---

## 9. References

**Core documentation**

- TraCI overview — https://eclipse.dev/sumo/docs/TraCI/index.html
- Vehicle Value Retrieval — https://eclipse.dev/sumo/docs/TraCI/Vehicle_Value_Retrieval.html
- Vehicle Signalling (bitmask) — https://eclipse.dev/sumo/docs/TraCI/Vehicle_Signalling.html
- Person Value Retrieval — https://eclipse.dev/sumo/docs/TraCI/Person_Value_Retrieval.html
- Simulation Value Retrieval — https://eclipse.dev/sumo/docs/TraCI/Simulation_Value_Retrieval.html
- Traffic Lights Value Retrieval — https://eclipse.dev/sumo/docs/TraCI/Traffic_Lights_Value_Retrieval.html
- Induction Loop Value Retrieval — https://eclipse.dev/sumo/docs/TraCI/Induction_Loop_Value_Retrieval.html
- Lane Area Detector Value Retrieval — https://eclipse.dev/sumo/docs/TraCI/Lane_Area_Detector_Value_Retrieval.html
- Protocol (wire format) — https://eclipse.dev/sumo/docs/TraCI/Protocol.html
- Generic Parameters — https://eclipse.dev/sumo/docs/TraCI/GenericParameters.html
- Object Variable Subscription — https://eclipse.dev/sumo/docs/TraCI/Object_Variable_Subscription.html
- Interfacing TraCI from Python — https://eclipse.dev/sumo/docs/TraCI/Interfacing_TraCI_from_Python.html

**Integration**

- Libsumo — https://eclipse.dev/sumo/docs/Libsumo.html
- Libtraci — https://eclipse.dev/sumo/docs/Libtraci.html

**Output**

- Statistic Output — https://eclipse.dev/sumo/docs/Simulation/Output/StatisticOutput.html
- FCD Output — https://eclipse.dev/sumo/docs/Simulation/Output/FCD_Output.html

**API**

- Python traci pydoc — https://sumo.dlr.de/pydoc/traci.html
- Source — https://github.com/eclipse-sumo/sumo

---

## 10. Summary

SUMO exposes far more telemetry than this project uses. The vehicle domain alone
provides acceleration, leader gaps, signal bitmask, lane-change intent and
reason, lateral position, and network-derived speed limits — most of which map
directly onto metrics that are currently approximated with weaker proxies.

Three concrete gaps stand out:

1. **Persons are not collected at all**, despite a pedestrian scenario existing.
2. **No ego is identified**, so `avg_speed` does not measure what it claims.
3. **Collisions and TTC are approximated geometrically** instead of using
   SUMO's authoritative collision events and a correct vector closing rate.

Fixing those three makes the validation loop substantially more credible. Moving
to subscriptions and libsumo is mechanical once the data model is right, and can
wait.
