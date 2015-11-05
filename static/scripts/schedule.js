// Global vars
var displayScheduleDate = new Date(Date.now() + cliTzOffset - devTzOffset); // dk
var displayScheduleTimeout;
var sid,sn,t;
var simdate = displayScheduleDate; // date for simulation
if (typeof progs !== 'undefined'){var nprogs = progs.length}; // number of programs
if (typeof nbrd !== 'undefined'){var nst = nbrd*8}; // number of stations

function scheduledThisDate(simdate) { // check if progrm is scheduled for this date and return list of start times
  // simdate is a JavaScript date object
  simday = Math.floor(simdate/(1000*3600*24)) // The number of days since epoc
  var sched_times = [];
  for(pid=0;pid<nprogs;pid++) { //for each program
    var pd=progs[pid];
    if((pd[0]&1)==0)
      continue; // program not enabled, do not match
    if ((pd[1]&0x80)&&(pd[2]>1)) {  // if interval program...  
      if(((simday)%pd[2])!=(pd[1]&0x7f))
        continue;	
    } else {
      var wd,dn,drem; // week day, Interval days, days remaining
      wd=(simdate.getDay()+6)%7; // getDay assumes sunday is 0, converts to Monday to 0 (weekday index)
      if((pd[1]&(1<<wd))==0)
        continue; // weekday checking
      dt=simdate.getDate(); // set dt = day of the month
      if((pd[1]&0x80)&&(pd[2]==0)) { // even day checking...
        if(dt%2)
          continue; // if odd day (dt%2 == 1), no not match
      }
      if((pd[1]&0x80)&&(pd[2]==1))  { // odd day checking...
        if(dt==31)
          continue; // if 31st of month, do not match
        else if (dt==29 && simdate.getMonth()==1)
          continue; // if leap year day, do not match
        else if (!(dt%2))
          continue; // if even day, do not match
      }
    }
    for (sched=pd[3]; sched < pd[4]; sched += pd[5]) {
      sched_times.push([sched*60, pid]);
    }
  }
  return sched_times;
}

function sortSched(sched_times) {
  // sort first by start time, then by program number
  sched_times.sort(function(a,b) {
    if (a[0] == b[0])
      if (a[1] == b[1])
        return 0;
     else if (a[1] > b[1])
       return 1;
     else
       return -1;
    else if (a[0] > b[0])
      return 1;
    else
      return -1;
    });
  return sched_times;
}

function doSimulation() { // Create schedule by a full program simulation, was draw_program()
  var bid,s,sid;
  var st_array=new Array(nst); //start time per station in seconds (minutes since midnight)?
  var et_array=new Array(nst); // end time per station (duration in seconds adjusted by water level)
  var ban_st_array=new Array(nst); // start time per station used for ban programs
  var ban_et_array=new Array(nst); // end time per station used for ban programs
  var seq_et_array=new Array(nst); // sequential end time per station
  var banstop_array=new Array(nst); // max ban stop for station
  var bandelay_array=new Array(nst); // max ban delay for station
  var schedule=[]; // shedule will hold data to display
  var banstarts=new Array(nst); // ordered list of ban starts for each station
  var program_times = scheduledThisDate(simdate);
  var last_seq_finish = 0;
  program_times = sortSched(program_times);
  for(sid=0;sid<nst;sid++)  { // for for each station...
    st_array[sid]=0;
    et_array[sid]=0;
    ban_et_array[sid]=0;
    ban_st_array[sid]=0;
    seq_et_array[sid]=0; 
    banstop_array[sid]=0; 
    bandelay_array[sid]=0; 
    banstarts[sid]=[];
  }
  while (program_times.length > 0) { // check through every program
    var sched = program_times.shift();
    var start = sched[0];
    var pid = sched[1];
    var pd = progs[pid];

    if ((pd[0]&4) == 0) {
      if (start < last_seq_finish+sdt) { // program will be delayed, so reinsert and try again
        program_times.push([last_seq_finish+sdt, pid]);
        program_times = sortSched(program_times);
        continue;
      }
    }

    if (last_seq_finish < start)
      last_seq_finish = start;

    for(sid=0;sid<nst;sid++) { // for each station...
      bid = sid>>3;
      s = sid % 8;
      if ((pd[7+bid]&(1<<s)) == 0)
        continue;

      duration = pd[6];
      if ((pd[0]&2) == 0 && (iw[bid]&(1<<s)) == 0) // adjust duration by water level
        duration = parseInt(duration*wl/100*wlx/100);

      if ((pd[0]&4) == 0) { // seq schedule
        st_array[sid] = Math.max(st_array[sid], last_seq_finish, bandelay_array[sid]);
        et_array[sid] = Math.max(et_array[sid], st_array[sid]+duration);

        if (st_array[sid] < banstop_array[sid]) // seq and stopped by ban?
          continue;
        seq_et_array[sid] = Math.max(seq_et_array[sid], et_array[sid]);
        last_seq_finish = Math.max(last_seq_finish, seq_et_array[sid]) + sdt;
      }
      else if ((pd[0]&2) == 0) { // fixed schedule
        st_array[sid] = Math.max(st_array[sid], bandelay_array[sid], start);
        et_array[sid] = Math.max(et_array[sid], st_array[sid]+duration);

        if (st_array[sid] < banstop_array[sid]) // fixed and stopped by ban?
          continue;
      }
      else {
        ban_st_array[sid] = Math.max(ban_st_array[sid], start);
        ban_et_array[sid] = Math.max(ban_et_array[sid], ban_st_array[sid]+duration);
        banstarts[sid].push({start:ban_st_array[sid]/60, program: pid+1, stop:ban_et_array[sid]/60});
        if ((pd[0]&8) == 8) // ban stop
          banstop_array[sid] = Math.max(banstop_array[sid], ban_et_array[sid]);
        else if ((pd[0]&16) == 16) // ban delay
          bandelay_array[sid] = Math.max(bandelay_array[sid], ban_et_array[sid]);
      }

      if ((pd[0]&2) == 0) // no ban
        schedule.push({ // data for creating home page program display
                      program: pid+1, // program number
                      station: sid, // station index
                      start: st_array[sid]/60, // start time, minutes since midnight
                      duration: et_array[sid]-st_array[sid], // duration in seconds
                      label: toClock(st_array[sid]/60, timeFormat) + " for " + toClock(((et_array[sid]/60)-(st_array[sid]/60)), 1) // ***not the same as log data date element
                    });
      else
        schedule.push({ // data for creating home page program display
                      program: pid+1, // program number
                      station: sid, // station index
                      start: ban_st_array[sid]/60, // start time, minutes since midnight
                      duration: ban_et_array[sid]-ban_st_array[sid], // duration in seconds
                      label: toClock(ban_st_array[sid]/60, timeFormat) + " for " + toClock(((ban_et_array[sid]/60)-(ban_st_array[sid]/60)), 1) // ***not the same as log data date element
                    });
    }
  }
  var added_entry = true;
  var save_banstarts = banstarts;
  while (added_entry) {
    var new_entries = [];
    added_entry = false;
    for (s in schedule) {
      var sched = schedule[s];
      var sid = sched.station;
      var prog_id = sched.program;
      var start = sched.start;
      var end = start + sched.duration/60;
      if (prog_id <= progs.length && (progs[prog_id-1][0]&2) == 0 && banstarts[sid].length > 0) {
        var entry = banstarts[sid][0];
        while (start > entry.start) {
          banstarts[sid].shift();
          if (banstarts[sid].length == 0)
            break;
          entry = banstarts[sid][0];
        }
        if (start <= entry.start && end > entry.start) {
          var actual_run_min = entry.start - start;
          var new_duration = schedule[s].duration - actual_run_min*60;
          var new_entry = {program: prog_id, station: sid, start: entry.stop, duration: new_duration,
                           label: toClock(entry.stop, timeFormat) + " for " + toClock(new_duration/60, 1)};
          schedule[s].duration = actual_run_min*60;
          schedule[s].label = toClock(start, timeFormat) + " for " + toClock(actual_run_min, 1);
          // **Add an entry for the remaining time.
          // This is a rough approximation.  It does not correctly factor in staggered seq stations in splitting. 
          // Do not delay into next day.
          if (entry.program <= progs.length && (progs[entry.program-1][0]&16) != 0 && new_entry.start < 24*60)
            new_entries.push(new_entry);
        }
      }
    }
    // If we split an entry rebuild and sort schedule list and then go see if it more splits occur.
    // Not terribly efficient but we assume ban delays are rare in practice.
    if (new_entries.length > 0) {
      added_entry = true;
      schedule = schedule.concat(new_entries);
      banstarts = save_banstarts;
      schedule.sort(function(a,b) {
          var diff = a.start - b.start;
          if (diff == 0)
            return a.station - b.station;
          else
            return diff;
          });
    }
  }
  return schedule;
}

function toXSDate(d) {
	var r = d.getFullYear() + "-" +
			(d.getMonth() < 9 ? "0" : "") + (d.getMonth()+1) + "-" +
			(d.getDate() < 10 ? "0" : "") + d.getDate();
	return r;
}

function toClock(duration, tf) {
	var h = Math.floor(duration/60);
	var m = Math.floor(duration - (h*60));
	if (tf == 0) {
		return (h>12 ? h-12 : h) + ":" + (m<10 ? "0" : "") + m + (h<12 ? "am" : "pm");
	} else {
		return (h<10 ? "0" : "") + h + ":" + (m<10 ? "0" : "") + m;
	}
}

function fromClock(clock) {
	var components = clock.split(":");
	var duration = 0;
	for (var c in components) {
		duration = duration*60 + parseInt(components[c], 10);
	}
	return duration;
}

function programName(p) {
	if (p == "Manual" || p == "Run-once") {
		return p + " Program";
	} else {
		return "Program " + p;
	}
}

// show timeline on home page
function displaySchedule(schedule) {
	if (displayScheduleTimeout != null) {
		clearTimeout(displayScheduleTimeout);
	}
	var now = new Date(Date.now() + cliTzOffset - devTzOffset); // will show device time
	var nowMark = now.getHours()*60 + now.getMinutes();
	var isToday = toXSDate(displayScheduleDate) == toXSDate(now);
	var programClassesUsed = new Object();
	jQuery(".stationSchedule .scheduleTick").each(function() {
		jQuery(this).empty();
		var sid = jQuery(this).parent().attr("data");
		var slice = parseInt(jQuery(this).attr("data"))*60;
		var boxes = jQuery("<div class='scheduleMarkerContainer'></div>");
		for (var s in schedule) {
			if (schedule[s].station == sid) {
				if (!(isToday && schedule[s].date == undefined && schedule[s].start + schedule[s].duration/60 < nowMark)) {
					var relativeStart = schedule[s].start - slice;
					var relativeEnd = schedule[s].start + schedule[s].duration/60 - slice;
					if (0 <= relativeStart && relativeStart < 60 ||
						0.05 < relativeEnd && relativeEnd <= 60 ||
						relativeStart < 0 && relativeEnd >= 60) {
						var barStart = Math.max(0,relativeStart)/60;
						var barWidth = Math.max(0.05,Math.min(relativeEnd, 60)/60 - barStart);
						var programClass;
						if (schedule[s].program == "Manual" || schedule[s].program == "Run-once") {
							programClass = "programManual";
						} else {
							programClass = "program" + (parseInt(schedule[s].program)+1)%10;
						}
						programClassesUsed[schedule[s].program] = programClass;
						var markerClass = (schedule[s].date == undefined ? "schedule" : "history");
						boxes.append("<div class='scheduleMarker " + programClass + " " + markerClass + "' style='left:" + barStart*100 + "%;width:" + barWidth*100 + "%' data='" + programName(schedule[s].program) + ": " + schedule[s].label + "'></div>");
					}
				}
			}
		}
		if (isToday && slice <= nowMark && nowMark < slice+60) {
			var stationOn = jQuery(this).parent().children(".stationStatus").hasClass("station_on");
			boxes.append("<div class='nowMarker" + (stationOn?" on":"")+ "' style='width:2px;left:"+ (nowMark-slice)/60*100 + "%;'>");
		}
		if (boxes.children().length > 0) {
			jQuery(this).append(boxes);
		}
	});
	jQuery("#legend").empty();
	for (var p in programClassesUsed) {
		jQuery("#legend").append("<span class='" + programClassesUsed[p] + "'>" + programName(p) + "</span>");
	}
	jQuery(".scheduleMarker").mouseover(scheduleMarkerMouseover);
	jQuery(".scheduleMarker").mouseout(scheduleMarkerMouseout);
	
	jQuery("#displayScheduleDate").text(dateString(displayScheduleDate) + (displayScheduleDate.getFullYear() == now.getFullYear() ? "" : ", " + displayScheduleDate.getFullYear()));
	if (isToday) {
		displayScheduleTimeout = setTimeout(displayProgram, 1*60*1000);  // every minute
	}
}

function displayProgram() { // Controls home page irrigation timeline
	//if (displayScheduleDate > devt) { //dk
	if (displayScheduleDate > new Date(Date.now() + cliTzOffset - devTzOffset)) { //dk
                var schedule = doSimulation();
		displaySchedule(schedule);
	} else {
		var visibleDate = toXSDate(displayScheduleDate);
		jQuery.getJSON(baseUrl + "/api/log?substation="+substation+ "&date="+visibleDate, function(log) {
			for (var l in log) {
				log[l].duration = fromClock(log[l].duration);
				log[l].start = fromClock(log[l].start)/60;
				if (log[l].date != visibleDate) {
					log[l].start -= 24*60;
				}
				log[l].label = toClock(log[l].start, timeFormat) + " for " + toClock(log[l].duration, 1);
			}
			if (toXSDate(displayScheduleDate) == toXSDate(new Date(Date.now() + cliTzOffset - devTzOffset))) {
                                var schedule = doSimulation();
				log = log.concat(schedule);
			}
			displaySchedule(log);
		})
	}
}

jQuery(document).ready(displayProgram);

function scheduleMarkerMouseover() {
	var description = jQuery(this).attr("data");
	var markerClass = jQuery(this).attr("class");
	markerClass = markerClass.substring(markerClass.indexOf("program"));
	markerClass = markerClass.substring(0,markerClass.indexOf(" "));
	jQuery(this).append('<span class="showDetails ' + markerClass + '">' + description + '</span>');
	jQuery(this).children(".showDetails").mouseover(function(){ return false; });
	jQuery(this).children(".showDetails").mouseout(function(){ return false; });
}
function scheduleMarkerMouseout() {
	jQuery(this).children(".showDetails").remove();
}
