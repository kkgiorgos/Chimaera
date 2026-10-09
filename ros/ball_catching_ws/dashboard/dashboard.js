"use strict";
(() => {
  const data = window.EXPERIMENT_DATA, byId = new Map(data.trials.map(t => [t.id, t]));
  const el = id => document.getElementById(id), format = (x, unit, digits=2) => Number.isFinite(x) ? `${x.toFixed(digits)} ${unit}` : "Unavailable";
  let viewer = null, model = null, current = null, playing = false, time = 0, last = null;
  const pointPass = p => p.expected_throws === 5 && p.status === "complete" && p.trials.length === 5 && p.trials.every(id => byId.get(id)?.summary.success);
  const completedPoints=data.points.filter(p=>p.expected_throws===5&&p.status==="complete");
  el("passed").textContent=completedPoints.length ? `${completedPoints.filter(pointPass).length} / ${completedPoints.length}` : "—";
  el("throws").textContent = data.trials.length;
  const cupReport=data.trials.length>0&&data.trials.every(t=>t.config.mode==="cup");
  const examples=data.points.length>0&&data.points.every(p=>p.expected_throws===1);
  if(cupReport){el("report-title").textContent="Cup catching";document.title="Chimaera · Cup catching";el("geometry-label").textContent="Launch position (m)";}
  if(examples){
    el("passed-label").textContent="Physical catches";
    el("passed").textContent=`${data.trials.filter(t=>t.summary.success).length} / ${data.trials.length}`;
    el("points-title").textContent=cupReport ? "Cup examples" : "Throw examples";
    el("report-description").textContent="Explore each throw, replay the robot's motion, and see the time available to react.";
    el("points-description").textContent="Select an example to replay its recorded motion and inspect the outcome. These individual demonstrations do not qualify a five-throw parameter point.";
  }
  function cell(row, text, cls="") { const td=document.createElement("td");td.textContent=text;td.className=cls;row.appendChild(td);return td; }
  const pipeline={
    physics:["Physics and stereo cameras", "Gazebo advances rigid-body dynamics and renders synchronized RGB images. Camera rate and the 1 ms physics step are configured simulation periods. Physics and render computation wall times are not recorded by this dashboard.", "src/ball_catching_sim/src/host.cpp · scene.py · /clock · ground_truth.csv"],
    transport:["Camera capture → matched stereo callback", "ros_gz_bridge delivers left and right images to ROS. Perception pairs identical capture timestamps and keeps bounded queues. The recorded age spans capture to receipt of a matched pair in simulation time; it combines transport, scheduling, and pair waiting. It does not isolate bridge CPU time.", "perception.jsonl: capture_time, receive_time · /stereo/{left,right}/image_raw"],
    perception:["Images → ball position, velocity, and size", "The callback prepares RGB views, detects the ball in both images, triangulates its metric position and radius, updates the flight filter, and publishes the usable estimate. The detailed substeps share one callback total. No detection skips estimation; an uninitialized filter can detect a ball before it produces a track.", "src/ball_catching_robot/src/perception.cpp + vision.cpp · perception.jsonl: *_wall_seconds · /robot/ball_state, /robot/ball_geometry"],
    planning:["Visual flight estimate → interception and braking", "The planner extrapolates the timestamped visual state, searches reachable cup heights and speed-matching trajectories, checks joint position, speed, acceleration and cup orientation, then publishes an approach and braking segment. Its outer timer runs at 30 Hz; cup searches are gated to at most one every 60 ms, so the desired recorded search rate is 16.7 Hz. Planning wall time measures each admitted search, including failed searches.", "src/ball_catching_robot/src/intercept.cpp + motion.cpp · interception.jsonl: planning_wall_seconds · /robot/joint_trajectory"],
    control:["Trajectory + joint feedback → bounded motor effort", "At 250 Hz, effort_control samples the active segment, computes dynamics and tracking corrections, and publishes bounded joint torques. Gazebo applies the torques and returns measured joints. The total measures native controller computation and publication; it excludes host physics and the timing-record write. Gripper mode also runs a separate finger controller.", "src/ball_catching_robot/src/effort_control.cpp · control_timing.jsonl: processing_wall_seconds · /robot/joint_states → /robot/effort_command"]
  };
  function showPipeline(stage){const reference=pipeline[stage];if(!reference)return;el("pipeline-title").textContent=reference[0];el("pipeline-description").textContent=reference[1];el("pipeline-source").textContent=reference[2];for(const button of el("pipeline").children)button.setAttribute("aria-pressed",button.dataset.stage===stage);}
  function detailedTiming(){
    el("timing-detail").replaceChildren();el("cadence").replaceChildren();
    if(!current){el("detail-note").textContent="Select a recorded throw.";return;}
    const selected=el("stage-filter").value;
    const rows=(current.summary.timing_detail||[]).filter(r=>selected==="all"||r.stage===selected);
    for(const detail of rows){const row=document.createElement("tr");const label=cell(row,detail.name);const clock=document.createElement("div");clock.className="clock";clock.textContent=`${detail.clock} clock`;label.appendChild(clock);row.title=`${detail.source}: ${detail.field}`;cell(row,detail.stats?.samples??0);cell(row,Number.isFinite(detail.achieved_hz)?detail.achieved_hz.toFixed(1):"—");cell(row,Number.isFinite(detail.desired_hz)?detail.desired_hz.toFixed(1):"—");cell(row,Number.isFinite(detail.rate_percent)?`${detail.rate_percent.toFixed(0)}%`:"—");for(const key of ["minimum_ms","median_ms","mean_ms","p95_ms","p99_ms","maximum_ms"])cell(row,Number.isFinite(detail.stats?.[key])?detail.stats[key].toFixed(3):"—");row.onclick=()=>showPipeline(detail.stage);el("timing-detail").appendChild(row);}
    const s=current.summary,counts=s.perception_counts,plans=s.planning_counts;
    el("detail-note").textContent=rows.length ? "Hover a row for its recording file and field; select it for the pipeline reference. Missing values were not recorded." : selected==="physics" ? "Physics and rendering wall time were not recorded. Their configured periods and the recorded physical motion remain available." : "No detailed records are available for this stage in this run.";
    if(counts){const p=document.createElement("p");p.textContent=`Matched pairs: ${counts.pairs} · detections: ${counts.detected} · usable tracks: ${counts.tracked}. Planning records: ${plans?.updates??0} · feasible plans: ${plans?.planned??0} · infeasible searches: ${plans?.infeasible??0}.`;el("cadence").appendChild(p);}
    for(const stage of ["perception","planning","control"]){if(selected!=="all"&&selected!==stage)continue;const detail=rows.find(r=>r.stage===stage&&r.cadence);if(detail){const p=document.createElement("p");p.textContent=`${stage[0].toUpperCase()+stage.slice(1)}: ${format(detail.achieved_hz,"Hz",1)} achieved / ${format(detail.desired_hz,"Hz",1)} desired (${format(detail.rate_percent,"%",0)} of target) · P95 update gap: ${format(detail.cadence.gaps.p95_ms,"ms",1)} (simulation clock).`;el("cadence").appendChild(p);}}
  }
  el("stage-filter").onchange=()=>{showPipeline(el("stage-filter").value);detailedTiming();};
  for(const button of el("pipeline").children)button.onclick=()=>{el("stage-filter").value=button.dataset.stage;showPipeline(button.dataset.stage);detailedTiming();};
  showPipeline("perception");
  function choosePoint(point, row) {
    for (const other of el("points").children) other.classList.toggle("selected", other===row);
    el("trial").replaceChildren();
    for (const [i, id] of point.trials.entries()) { const t=byId.get(id);if(!t)continue;const o=document.createElement("option");o.value=id;o.textContent=`Throw ${i+1} · ${t.summary.success ? "caught" : "miss"}`;el("trial").appendChild(o); }
    if (el("trial").options.length) chooseTrial(byId.get(el("trial").value));
    else { playing=false;current=null;el("play").textContent="Play";el("scrub").value=0;el("time").textContent="0.000 s";el("timeline").replaceChildren();el("timing-note").textContent="";el("replay-note").textContent="No recorded motion for this point.";viewer?.setTrail([]);el("status").textContent=point.reason || "No completed throws for this data point.";el("flight").textContent="—";el("reaction").textContent="—";el("timing").replaceChildren();el("overview").replaceChildren();if(viewer)viewer.setFrame({},[-5,0,.04]); }
  }
  for (const point of data.points) {
    const row=document.createElement("tr"), config=point.config||{}, catches=point.trials.filter(id=>byId.get(id)?.summary.success).length;
    const distance=config.launch_position && config.target ? Math.hypot(...config.launch_position.map((x,i)=>x-config.target[i])) : null;
    cell(row,point.label||point.id);cell(row,format(config.launch_speed,"m/s"),"number");cell(row,cupReport&&config.launch_position ? `[${config.launch_position.map(x=>x.toFixed(2)).join(", ")}]` : format(distance,"m"),"number");cell(row,`${catches}/${point.expected_throws}`,"number");
    const label=point.status==="invalid" ? "Invalid throw" : point.status!=="complete" ? "Incomplete" : pointPass(point) ? "Pass" : point.expected_throws===1 ? (catches===1 ? "Caught" : "Miss") : "Fail";
    cell(row,label,pointPass(point)||label==="Caught"?"good":label==="Fail"||label==="Miss"?"bad":"muted");row.tabIndex=0;row.setAttribute("aria-label",`${point.label||point.id}, ${label}`);row.onclick=()=>choosePoint(point,row);row.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();choosePoint(point,row);}};el("points").appendChild(row);
  }
  function chooseTrial(trial) {
    current=trial;time=0;playing=false;el("play").textContent="Play";
    const s=trial.summary;el("flight").textContent=format(s.flight_seconds,"s");const reaction=s.bounce_count>0 ? s.post_bounce_reaction_seconds : s.reaction_seconds;
    el("reaction-label").textContent=s.bounce_count>0 ? "Visual window after final bounce" : "Visual reaction window";
    el("reaction").textContent=format(Number.isFinite(reaction)?reaction*1000:null,"ms",0);
    el("status").textContent=`${s.success?"Caught":"Miss"} · ${s.reason.replaceAll("_"," ")} · ${s.bounce_count || 0} bounces · launch ${format(s.launch_speed,"m/s")} · arrival ${format(s.arrival_speed,"m/s")}`;
    el("status").className=s.success?"good":"bad";
    el("scrub").max=trial.frames.at(-1).time;el("scrub").value=0;
    if (model!==trial.model) {
      viewer?.destroy();model=trial.model;
      try { viewer=new window.ReplayViewer(el("replay"),data.models[model]); }
      catch(error){viewer=null;el("replay-note").textContent=`3D replay unavailable: ${error.message}. Recorded timing and outcomes remain available.`;}
    }
    viewer?.setCourt(Boolean(trial.config.court));
    el("court-view").textContent=trial.config.court ? "Court view" : "Wide view";
    viewer?.setTrail(trial.frames.map(frame=>frame.ball));
    const processing=Object.entries(s.processing).filter(([name])=>trial.config.mode!=="cup"||name!=="Gripper control");const max=Math.max(.01,...processing.map(([,v])=>v?.p95_ms||0));el("timing").replaceChildren();
    for(const [name,stats] of processing){const row=document.createElement("div");row.className="timing-row";const title=document.createElement("span");title.textContent=name;const track=document.createElement("div");track.className="bar-track";const bar=document.createElement("div");bar.className="bar";bar.style.width=`${stats ? stats.p95_ms/max*100 : 0}%`;track.appendChild(bar);const value=document.createElement("span");value.textContent=format(stats?.p95_ms,"ms",3);row.append(title,track,value);el("timing").appendChild(row);}
    el("timing-note").textContent=`Observation age (95th percentile): ${format(s.observation_age?.p95_ms,"ms",1)}.${s.arm_timing_sampled ? " Arm-control timing is sampled in this older run." : ""} Unavailable values were not recorded.`;
    el("overview").replaceChildren();for(const [name,value] of [["Starting condition",s.start_condition==="vertical_home" ? "Vertical home" : s.start_condition==="upright_home" ? "Previous bent home" : s.start_condition==="historical_prepared_pose" ? "Historical prepared/custom pose" : "Unrecorded"],["Hand position at launch",s.hand_position_at_launch ? `[${s.hand_position_at_launch.map(x=>x.toFixed(3)).join(", ")}] m` : "Unavailable"],["Hand displacement to flight end",format(Number.isFinite(s.hand_displacement_to_flight_end)?s.hand_displacement_to_flight_end*100:null,"cm")],...(Number.isFinite(s.visual_approach_seconds) ? [["Visual approach begins",format(s.visual_approach_seconds,"s after launch",3)],["Planned approach duration",format(s.visual_approach_duration,"s",3)]] : []),["Launch speed",format(s.launch_speed,"m/s")],[s.arrival_speed_kind==="before_inferred_impact" ? "Arrival speed before inferred impact" : "Measured arrival speed",format(s.arrival_speed,"m/s")],["Flight to "+s.end_kind.replaceAll("_"," "),format(s.flight_seconds,"s",3)],["First usable visual track",format(s.first_track_seconds,"s after launch",3)],["First visual track → interception",format(s.reaction_seconds===null?null:s.reaction_seconds*1000,"ms",1)],["First plan time remaining",format(s.first_plan_lead_ms,"ms",1)],...(s.bounce_count ? [["Ground bounces",`${s.bounce_count} / ${trial.config.allowed_bounces || 0} allowed`],["Track after final bounce",format(s.post_bounce_track_seconds,"s",3)],["Visual window after final bounce",format(s.post_bounce_reaction_seconds===null?null:s.post_bounce_reaction_seconds*1000,"ms",1)]] : []),["Retention requirement",format(trial.result.retention,"s")],...(trial.config.present ? [["Presentation end",s.presentation_success===true ? "Ball held" : s.presentation_success===false ? "Ball lost" : "Unavailable"]] : [])]){const row=document.createElement("tr");cell(row,name,"muted");cell(row,value,"number");el("overview").appendChild(row);}
    if(trial.config.mode==="cup")for(const row of el("overview").children)row.cells[0].textContent=row.cells[0].textContent.replace(/^Hand /,"Cup ");
    if(Number.isFinite(s.home_error_at_launch_degrees)){const row=document.createElement("tr");cell(row,"Maximum joint error from home at launch","muted");cell(row,format(s.home_error_at_launch_degrees,"°",3),"number");el("overview").appendChild(row);}
    if(Number.isFinite(s.braking_duration))for(const [name,value]of [["Planned cup speed at entry",format(s.planned_cup_speed,"m/s",3)],["Planned braking starts",format(s.braking_start_seconds,"s after launch",3)],["Planned braking duration",format(s.braking_duration,"s",3)]]){const row=document.createElement("tr");cell(row,name,"muted");cell(row,value,"number");el("overview").appendChild(row);}
    el("timeline").replaceChildren();
    const end=trial.frames.at(-1).time;
    const events=[["Launch",0],["Visual track",s.first_track_seconds],
                  ...(trial.bounces||[]).map((b,i)=>[`Bounce ${i+1}`,b.time]),
                  ["Flight ends",s.flight_seconds],
                  ["Brake starts (planned)",s.braking_start_seconds],
                  ["Stopped (planned)",s.braking_end_seconds]].filter(([,t])=>Number.isFinite(t));
    el("timeline").style.height=`${40+events.length*20}px`;
    for(const [index,[name,t]] of events.entries()){
      const marker=document.createElement("div");marker.className="event";
      marker.style.left=`${Math.min(100,t/end*100)}%`;
      const label=document.createElement("span");label.textContent=`${name} ${t.toFixed(2)}s`;
      label.style.top=`${19+index*20}px`;marker.appendChild(label);el("timeline").appendChild(marker);
    }
    if(viewer)el("replay-note").textContent=`Replay starts at launch. ${s.start_condition==="vertical_home" ? "The arm starts vertically with only the elbow bend required by joint limits. The empty cup rotates upward during the visual approach; interception and braking follow during flight." : s.start_condition==="upright_home" ? "Previous run: the arm starts from the earlier bent home pose." : s.start_condition==="historical_prepared_pose" ? "Historical run: the robot starts from a prepared/custom pose, not upright home." : "The starting condition was not recorded."} Recorded motion is interpolated between samples. Gold shows the recorded path; the cyan ball locator is enlarged for visibility.`;
    detailedTiming();draw();
  }
  function draw(){if(!current)return;const frames=current.frames;let lo=0,hi=frames.length-1;while(lo+1<hi){const mid=(lo+hi)>>1;if(frames[mid].time<=time)lo=mid;else hi=mid;}const a=frames[lo],b=frames[hi],mix=Math.min(1,Math.max(0,(time-a.time)/(b.time-a.time||1)));const joints={};for(const [name,value]of Object.entries(a.joints))joints[name]=value+mix*((b.joints[name]??value)-value);const ball=a.ball.map((x,i)=>x+mix*(b.ball[i]-x));viewer?.setFrame(joints,ball);el("scrub").value=time;el("time").textContent=`${time.toFixed(3)} s`;}
  el("trial").onchange=()=>chooseTrial(byId.get(el("trial").value));el("scrub").oninput=()=>{time=Number(el("scrub").value);draw();};el("play").onclick=()=>{if(!current)return;if(time>=current.frames.at(-1).time)time=0;playing=!playing;el("play").textContent=playing?"Pause":"Play";};
  for(const name of ["robot","court"])el(`${name}-view`).onclick=()=>{viewer?.setView(name);el("robot-view").setAttribute("aria-pressed",name==="robot");el("court-view").setAttribute("aria-pressed",name==="court");};
  function tick(now){if(playing&&current&&last!==null){time=Math.min(current.frames.at(-1).time,time+(now-last)/1000*Number(el("rate").value));if(time>=current.frames.at(-1).time){playing=false;el("play").textContent="Play";}draw();}last=now;requestAnimationFrame(tick);}requestAnimationFrame(tick);
  if(data.points.length){const index=Math.max(0,data.points.findIndex(p=>p.trials.some(id=>byId.has(id))));choosePoint(data.points[index],el("points").children[index]);}if(!data.trials.length)el("empty").style.display="block";
})();
