// SoundShredder for Premiere Pro: timeline helpers the panel calls (ExtendScript / ES3).
// Every function takes and returns JSON text. Times travel as tick strings
// (254016000000 per second) so nothing loses precision on long timelines.

var SS_TICKS = 254016000000;
var SS_BIN = "SoundShredder";
var SS_ALL_MEDIA = 4;

function ssQuote(text) {
    return '"' + String(text).replace(/[\\"\x00-\x1f]/g, function (c) {
        var named = { '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t" };
        return named[c] || "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4);
    }) + '"';
}

function ssJSON(value) {
    if (value === null || value === undefined) return "null";
    var type = typeof value;
    if (type === "number") return isFinite(value) ? String(value) : "null";
    if (type === "boolean") return value ? "true" : "false";
    if (type === "string") return ssQuote(value);
    var parts = [];
    if (value instanceof Array) {
        for (var i = 0; i < value.length; i++) parts.push(ssJSON(value[i]));
        return "[" + parts.join(",") + "]";
    }
    for (var key in value) {
        if (value.hasOwnProperty(key)) parts.push(ssQuote(key) + ":" + ssJSON(value[key]));
    }
    return "{" + parts.join(",") + "}";
}

function ssParse(text) {
    return eval("(" + text + ")"); // only ever the panel's own JSON
}

function ssTime(ticks) {
    var time = new Time();
    time.ticks = String(Math.round(ticks));
    return time;
}

function ssTicksOf(time) {
    return Number(time.ticks);
}

function ssSequenceById(sequenceId) {
    var sequences = app.project.sequences;
    for (var i = 0; i < sequences.numSequences; i++) {
        if (sequences[i].sequenceID === sequenceId) return sequences[i];
    }
    return null;
}

function ssFindByPath(parent, path) {
    for (var i = 0; i < parent.children.numItems; i++) {
        var child = parent.children[i];
        if (child.type === ProjectItemType.BIN) {
            var found = ssFindByPath(child, path);
            if (found) return found;
        } else {
            try {
                if (child.getMediaPath() === path) return child;
            } catch (e) {}
        }
    }
    return null;
}

function ssBin(name) {
    var root = app.project.rootItem;
    for (var i = 0; i < root.children.numItems; i++) {
        var child = root.children[i];
        if (child.type === ProjectItemType.BIN && child.name === name) return child;
    }
    var bin = root.createBin(name);
    return bin || ssBin(name);
}

// Import any of these files the project does not have yet; return the project items in order.
function ssImport(paths, binName) {
    var items = [], missing = [], imported = [], i;
    for (i = 0; i < paths.length; i++) {
        items.push(ssFindByPath(app.project.rootItem, paths[i]));
        if (!items[i]) missing.push(paths[i]);
    }
    if (missing.length) {
        var bin = ssBin(binName || SS_BIN);
        app.project.importFiles(missing, true, bin, false);
        for (i = 0; i < paths.length; i++) {
            if (!items[i]) {
                items[i] = ssFindByPath(bin, paths[i]) || ssFindByPath(app.project.rootItem, paths[i]);
                if (items[i]) imported.push(paths[i]);
            }
        }
    }
    return { items: items, imported: imported };
}

function ssProjectPath() {
    try {
        return ssJSON({ path: app.project ? app.project.path : "" });
    } catch (e) {
        return ssJSON({ path: "" });
    }
}

// Clips selected in the active sequence that can be separated, one entry per placed instance.
function ss_getSelection() {
    try {
        if (!app.project) return ssJSON({ error: "Open a project first." });
        var sequence = app.project.activeSequence;
        if (!sequence) return ssJSON({ error: "Open a sequence and select the clips to separate." });
        var selection = sequence.getSelection();
        var clips = [], skipped = [], seen = {};
        for (var i = 0; i < selection.length; i++) {
            var item = selection[i];
            var source = item.projectItem;
            if (!source) continue;
            var path = "";
            try {
                path = source.getMediaPath();
            } catch (e) {}
            if (!path) {
                skipped.push(item.name + " (not a media file)");
                continue;
            }
            var key = source.nodeId + "@" + item.start.ticks;
            if (seen[key]) continue; // the video and audio halves of one linked clip
            seen[key] = true;
            var speed = 1, reversed = false;
            try {
                speed = item.getSpeed();
                reversed = item.isSpeedReversed() ? true : false;
            } catch (e) {}
            if (Math.abs(speed - 1) > 0.0001 || reversed) {
                skipped.push(item.name + " (speed changed)");
                continue;
            }
            clips.push({
                path: path,
                name: item.name,
                nodeId: source.nodeId,
                start: item.start.ticks,
                end: item.end.ticks,
                inPoint: item.inPoint.ticks,
                outPoint: item.outPoint.ticks
            });
        }
        return ssJSON({ sequenceId: sequence.sequenceID, sequence: sequence.name, clips: clips, skipped: skipped });
    } catch (e) {
        return ssJSON({ error: "Could not read the selection: " + e });
    }
}

function ssTrackFree(track, ranges) {
    if (track.isLocked()) return false;
    for (var i = 0; i < track.clips.numItems; i++) {
        var clip = track.clips[i];
        var start = ssTicksOf(clip.start), end = ssTicksOf(clip.end);
        for (var r = 0; r < ranges.length; r++) {
            if (start < ranges[r][1] && end > ranges[r][0]) return false;
        }
    }
    return true;
}

// The first run of `count` neighbouring audio tracks that is empty wherever the stems go.
// The run may continue past the last track; the missing tracks are added at the bottom.
function ssStemTracks(sequence, count, ranges) {
    var tracks = sequence.audioTracks, before = tracks.numTracks, base = 0;
    for (; base < before; base++) {
        var free = true;
        for (var k = 0; k < count && base + k < before && free; k++) free = ssTrackFree(tracks[base + k], ranges);
        if (free) break;
    }
    var missing = base + count - before;
    if (missing > 0) {
        if (!app.project.activeSequence || app.project.activeSequence.sequenceID !== sequence.sequenceID) {
            app.project.openSequence(sequence.sequenceID);
        }
        app.enableQE();
        qe.project.getActiveSequence().addTracks(0, sequence.videoTracks.numTracks, missing, 1, before, 0, 1, 0);
        if (sequence.audioTracks.numTracks < base + count) throw new Error("Could not add audio tracks for the stems.");
    }
    return base;
}

function ssSetInOut(item, inTicks, outTicks) {
    try {
        item.setInPoint(ssTime(inTicks), SS_ALL_MEDIA);
        item.setOutPoint(ssTime(outTicks), SS_ALL_MEDIA);
    } catch (e) {
        item.setInPoint(inTicks / SS_TICKS, SS_ALL_MEDIA);
        item.setOutPoint(outTicks / SS_TICKS, SS_ALL_MEDIA);
    }
}

function ssClipAt(track, item, startTicks) {
    for (var i = 0; i < track.clips.numItems; i++) {
        var clip = track.clips[i];
        if (Math.abs(ssTicksOf(clip.start) - startTicks) < SS_TICKS / 1000 && clip.projectItem.nodeId === item.nodeId) {
            return clip;
        }
    }
    return null;
}

// args: {sequenceId, files: [dialogue, music, sfx], offsetTicks, durationTicks, clips, muteOriginal}
function ss_placeStems(argsText) {
    try {
        var args = ssParse(argsText);
        var sequence = ssSequenceById(args.sequenceId);
        if (!sequence) return ssJSON({ error: "The sequence was closed or deleted." });
        var offset = Number(args.offsetTicks), duration = Number(args.durationTicks);
        var placements = [], ranges = [], warnings = [], i, k;
        for (i = 0; i < args.clips.length; i++) {
            var clip = args.clips[i];
            var stemIn = Number(clip.inPoint) - offset, start = Number(clip.start);
            if (stemIn < 0) {
                start -= stemIn; // the clip begins before the audio does
                stemIn = 0;
            }
            var stemOut = Math.min(Number(clip.outPoint) - offset, duration);
            if (stemOut <= stemIn) {
                warnings.push(clip.name + " has no audio in its trimmed range.");
                continue;
            }
            placements.push({ clip: clip, start: start, stemIn: stemIn, stemOut: stemOut });
            ranges.push([start, start + stemOut - stemIn]);
        }
        if (!placements.length) return ssJSON({ placed: 0, warnings: warnings });
        var stems = ssImport(args.files, SS_BIN).items;
        for (k = 0; k < stems.length; k++) {
            if (!stems[k]) return ssJSON({ error: "Premiere could not import " + args.files[k] });
        }
        var base = ssStemTracks(sequence, stems.length, ranges);
        var placed = 0;
        for (k = 0; k < stems.length; k++) {
            var track = sequence.audioTracks[base + k];
            for (i = 0; i < placements.length; i++) {
                var place = placements[i];
                ssSetInOut(stems[k], place.stemIn, place.stemOut);
                track.overwriteClip(stems[k], String(Math.round(place.start)));
                var added = ssClipAt(track, stems[k], place.start);
                if (!added) {
                    warnings.push("A stem of " + place.clip.name + " could not be placed on A" + (base + k + 1) + ".");
                    continue;
                }
                placed++;
            }
            try {
                stems[k].clearInPoint();
                stems[k].clearOutPoint();
            } catch (e) {}
        }
        var muted = 0, unmuted = 0;
        if (args.muteOriginal) {
            for (var t = 0; t < sequence.audioTracks.numTracks; t++) {
                if (t >= base && t < base + stems.length) continue;
                var clips = sequence.audioTracks[t].clips;
                for (var c = 0; c < clips.numItems; c++) {
                    var item = clips[c], a = ssTicksOf(item.start), b = ssTicksOf(item.end);
                    for (i = 0; i < placements.length; i++) {
                        var source = placements[i].clip;
                        if (item.projectItem && item.projectItem.nodeId === source.nodeId &&
                            a < Number(source.end) && b > Number(source.start) && !item.disabled) {
                            try {
                                item.disabled = true;
                            } catch (e) {}
                            if (item.disabled) muted++;
                            else unmuted++;
                            break;
                        }
                    }
                }
            }
        }
        if (unmuted) warnings.push("Premiere did not let SoundShredder mute " + unmuted + " original audio clip(s); disable them by hand.");
        return ssJSON({ placed: placed, firstTrack: base + 1, trackCount: stems.length, muted: muted, warnings: warnings });
    } catch (e) {
        return ssJSON({ error: "Placing the stems failed: " + e + (e.line ? " (line " + e.line + ")" : "") });
    }
}

// args: {paths, bin} — used to bring watch-folder results into the open project.
function ss_importFiles(argsText) {
    try {
        var args = ssParse(argsText);
        if (!app.project) return ssJSON({ error: "Open a project first." });
        var result = ssImport(args.paths, args.bin || SS_BIN);
        var found = [];
        for (var i = 0; i < args.paths.length; i++) if (result.items[i]) found.push(args.paths[i]);
        return ssJSON({ imported: result.imported, present: found, project: app.project.path });
    } catch (e) {
        return ssJSON({ error: "Import failed: " + e });
    }
}

function ss_projectPath() {
    return ssProjectPath();
}
