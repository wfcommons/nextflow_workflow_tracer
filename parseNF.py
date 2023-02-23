import os
import sys
import re
import networkx as nx
import json
from datetime import datetime, timezone
import subprocess

##################################################################################################################

#Parse filepath_stdout
def parse_stdout(filepath_stdout, workflow):
    with open(filepath_stdout, "r") as fp:
        for count, line in enumerate(fp):
            if "version" in line:
                x = line.split("version")[1].strip()
                print("Nextflow version:\n\t{}".format(x))

            elif "Launching" in line:
                x = line.split("`")[1].strip()
                #print("Workflow repo:\n\t{}".format(x))
                workflow["repo"] = x

            elif "Completed at" in line:
                x = line[line.find(":")+1:].strip() + " -1000"      #HST is -10 hours to UTC
                y = datetime.strptime(x, "%d-%b-%Y %H:%M:%S %z")

                #dunno if %d (zero-padded day of the month) or (%-d not zero-padded)
                #of if %H (zero-padded 24 hour clock hour) or (%-H not zero-padded)
                #%S (seconds) or (%-S)
                #print("executedAt:\n\t{}".format(x))
                #print("converted:\n\t{}".format(y.isoformat()))

                workflow["executedAt"] = str(y.isoformat())

            elif "Duration" in line:
                x = line[line.find(":")+1:].strip()
                print("makespan:\n\t{}".format(x))

                seconds = 0
                temp    = x
                if "h" in line:
                    seconds += int(temp.split("h")[0].strip()) * 60 * 60
                    temp     = temp.split("h")[1].strip()
                if "m" in line:
                    seconds += int(temp.split("m")[0].strip()) * 60
                    temp     = temp.split("m")[1].strip()
                if "s" in line:
                    seconds += int(temp.split("s")[0].strip())

                workflow["makespan"] = seconds

#Parse filepath_trace
#Assumes the following trace fields
#0,       1,       2,        3,    4,   5,     6,     7,          8,           9
#process, task_id, realtime, %cpu, rss, rchar, wchar, read_bytes, write_bytes, workdir
def parse_trace(filepath_trace, processes, task_id, realtime, pct_cpu, rss, rchar, wchar, read_bytes, write_bytes, work_dir):
    with open(filepath_trace, "r") as fp:
        for count, line in enumerate(fp):
            if count == 0:      #skip first line
                continue

            fields = line.split("\t")
            #print("Line {}: {}".format(count, fields))

            process = fields[0].strip()
            processes.append(process)
            
            task_id [process] = fields[1].strip()
            realtime[process] = fields[2].strip()
            pct_cpu [process] = fields[3].strip()
            rss     [process] = fields[4].strip()

            rchar      [process] = fields[5].strip()
            wchar      [process] = fields[6].strip()
            read_bytes [process] = fields[7].strip()
            write_bytes[process] = fields[8].strip()

            work_dir[process] = fields[9].strip()

#Parse filepath_scripts
def parse_scripts(filepath_scripts, scripts):
    with open(filepath_scripts, "r") as fp:
        file_string = ""
        for count, line in enumerate(fp):
            file_string += line

        x = file_string.split("END_PROCESS_SCRIPT")

        for i in range(len(x)-1):       #last line in x is blank
            y = x[i].split("START_PROCESS_SCRIPT")

            scripts[y[0].strip()] = y[1].strip()

#Parse filepath_dag file
def parse_dag(filepath_dag, parents, children):
    G = nx.nx_agraph.read_dot(filepath_dag)

    def check_graph(G):     #checks if the graph contains a vertex that is not a process (i.e., does not have a label)
        for node in G:
            if "label" not in G.nodes[node]:
                return 1
        return 0

    while (check_graph(G) == 1):
        for node in G:
            if "label" not in G.nodes[node]:
                in_nodes  = G.pred[node].keys()
                out_nodes = G.succ[node].keys()
                G.remove_node(node)

                for in_node in in_nodes:
                    for out_node in out_nodes:
                        #print("\t({}, {})".format(in_node, out_node))
                        G.add_edge(in_node, out_node)

                break


    for node in G:
        #print("\nnode {}: {}".format(node, G.nodes[node]))
        process = G.nodes[node]["label"]

        parents [process] = []
        children[process] = []

        if G.in_degree[node] > 0:
            # print("\tpred: {}".format(list(G.pred[node].keys())))
            for i, pred in enumerate(G.pred[node].keys()):
                #print("pred {} = {}".format(pred, G.nodes[pred]["label"]))
                parents[process].append(G.nodes[pred]["label"])

        if G.out_degree[node] > 0:
            # print("\tsucc: {}".format(list(G.succ[node].keys())))
            for i, succ in enumerate(G.succ[node].keys()):
                #print("succ {} = {}".format(succ, G.nodes[succ]["label"]))
                children[process].append(G.nodes[succ]["label"])

#Parse filepath_log
def parse_log(filepath_log, processes, files, file_bytes_read, file_bytes_write):
    for i, process in enumerate(processes):
        #print("\nParsing log for process {}".format(process))

        files[process] = []
        remote_files   = {}     #stores (path, index in files) for files that are not stored locally on the computer

        file_bytes_read [process] = 0.
        file_bytes_write[process] = 0.

        with open(filepath_log, "r") as fp:
            for count, line in enumerate(fp):
                if process in line:
                    if "Message arrived" in line:
                        x = ((line.split("--"))[1].split("=>"))[1].split(",")

                        for item in x:
                            temp = item.strip().replace("[", "").replace("]", "")
                            if "." in temp or "/" in temp:
                                curr_file = {}
                                curr_file["link"] = "input"

                                x = temp.split("/")
                                curr_file["name"] = x[len(x)-1]
                                curr_file["path"] = "/".join(x[:len(x)-1]) + "/"

                                if not os.path.exists(temp):
                                    curr_file["path"] = "/".join(x[:len(x)-1]) + "/"
                                    remote_files[temp] = len(files[process])        #input file pulled from nf-core test-datasets
                                    curr_file["size"] = "TODO - Remote file"
                                else:
                                    curr_file["path"] = "/" + "/".join(x[len(x)-3:len(x)-1]) + "/"

                                    if os.path.isfile(temp):
                                        curr_file["size"] = float(os.path.getsize(temp))/1000.      #in KB (not KiB!)
                                        file_bytes_read[process] += float(curr_file["size"])
                                        
                                    elif os.path.isdir(temp):
                                        #print("{} is a directory".format(temp))
                                        total_size = 0
                                        for dpath, dname, fnames in os.walk(temp):
                                            for f in fnames:
                                                x = os.path.join(dpath, f)

                                                if os.path.isfile(x):
                                                    total_size += os.path.getsize(x)
                                                elif os.path.islink(x):
                                                    total_size += os.path.getsize(os.readlink(x))

                                        curr_file["size"] = float(total_size)/1000.      #in KB (not KiB!)
                                        file_bytes_read[process] += float(curr_file["size"])

                                    elif os.path.islink(temp):
                                        #print("{} is a symbolic link".format(temp))
                                        curr_file["size"] = os.path.getsize(os.readlink(temp))
                                        file_bytes_read[process] += float(curr_file["size"])

                                    else:
                                        print("{} exists! (but dunno what it is)".format(temp))

                                files[process].append(curr_file)

                    elif "Binding out param:" in line:
                        #print("Line {}:\n{}".format(count, line))
                        x = ((line.split("Binding out param:"))[1].split("="))[1].split(",")

                        for item in x:
                            temp = item.strip().replace("[", "").replace("]", "")
                            if "." in temp or "/" in temp:
                                curr_file = {}
                                curr_file["link"] = "output"

                                x = temp.split("/")
                                curr_file["name"] = x[len(x)-1]

                                curr_file["path"] = "/" + "/".join(x[len(x)-3:len(x)-1]) + "/"
                                # if work_dir[process] in temp:
                                #     print("trimmed path = {}".format(temp[temp.find(work_dir[process]):]))
                                #     curr_file["path"] = temp[temp.find(work_dir[process]):]#"/".join(x[:len(x)-1]) + "/"
                                # else:
                                #     curr_file["path"] = "/".join(x[:len(x)-1]) + "/"
                                
                                #note output files should always be written to tasks work dir (i.e., no remote files)
                                if os.path.exists(temp):
                                    if os.path.isfile(temp):
                                        curr_file["size"] = float(os.path.getsize(temp))/1000.      #in KB (not KiB!)
                                        file_bytes_write[process] += float(curr_file["size"])
                                        
                                    elif os.path.isdir(temp):
                                        #print("{} is a directory".format(temp))
                                        total_size = 0
                                        for dpath, dname, fnames in os.walk(temp):
                                            for f in fnames:
                                                x = os.path.join(dpath, f)

                                                if os.path.isfile(x):
                                                    total_size += os.path.getsize(x)
                                                elif os.path.islink(x):
                                                    total_size += os.path.getsize(os.readlink(x))

                                        curr_file["size"] = float(total_size)/1000.      #in KB (not KiB!)
                                        file_bytes_write[process] += float(curr_file["size"])

                                    elif os.path.islink(temp):
                                        #print("{} is a symbolic link".format(temp))
                                        curr_file["size"] = os.path.getsize(os.readlink(temp))
                                        file_bytes_write[process] += float(curr_file["size"])

                                    else:
                                        print("{} exists! (but dunno what it is)".format(temp))

                                files[process].append(curr_file)
            
        if len(remote_files) > 0:
            with open(filepath_log, "r") as fp:
                for count, line in enumerate(fp):
                    if "Copying foreign file" in line:
                        for j, rfile in enumerate(remote_files):
                            if rfile in line:
                                #print("Line {}: {}".format(count, line))
                                temp = line.split(rfile + " to work dir:")[1].strip()
                                #print("\tremote file {} ==> {}".format(rfile, temp))

                                if os.path.exists(temp):
                                    if os.path.isfile(temp):
                                        files[process][remote_files[rfile]]["size"] = float(os.path.getsize(temp))/1000.      #in KB (not KiB!)
                                        
                                        if files[process][remote_files[rfile]]["link"] == "input":
                                            file_bytes_read[process] += float(curr_file["size"])
                                        else:
                                            file_bytes_write[process] += float(curr_file["size"])

                                    elif os.path.isdir(temp):
                                        #print("{} is a directory".format(temp))
                                        total_size = 0
                                        for dpath, dname, fnames in os.walk(temp):
                                            for f in fnames:
                                                x = os.path.join(dpath, f)

                                                if os.path.isfile(x):
                                                    total_size += os.path.getsize(x)
                                                elif os.path.islink(x):
                                                    total_size += os.path.getsize(os.readlink(x))

                                        files[process][remote_files[rfile]]["size"] = float(total_size)/1000.      #in KB (not KiB!)
                                        if files[process][remote_files[rfile]]["link"] == "input":
                                            file_bytes_read[process] += float(curr_file["size"])
                                        else:
                                            file_bytes_write[process] += float(curr_file["size"])

                                    elif os.path.islink(temp):
                                        #print("{} is a symbolic link".format(temp))
                                        files[process][remote_files[rfile]]["size"] = os.path.getsize(os.readlink(temp))
                                        if files[process][remote_files[rfile]]["link"] == "input":
                                            file_bytes_read[process] += float(curr_file["size"])
                                        else:
                                            file_bytes_write[process] += float(curr_file["size"])

                                    else:
                                        print("{} exists! (but dunno what it is)".format(temp))

##################################################################################################################

#0. Command line arguments
argc = len(sys.argv)
if (argc != 8):
    print(f"Usage: python3 {sys.argv[0]} <workflow name> <log file> <trace file> <scripts file> <dag file> <stdout file> <output file>")
    quit()

workflow_name    = str(sys.argv[1]) #'hlatyping'
filepath_log     = str(sys.argv[2]) #'hlatyping-log.txt'
filepath_trace   = str(sys.argv[3]) #'hlatyping-trace.txt'
filepath_scripts = str(sys.argv[4]) #'hlatyping-scripts.txt'
filepath_dag     = str(sys.argv[5]) #'hlatyping-dag.dot'
filepath_stdout  = str(sys.argv[6]) #'hlatyping-stdout.txt'
outfile          = str(sys.argv[7]) #"wfcommons-" + workflow_name + ".json"

print(f"Command line arguments:\n\t{workflow_name=}\n\t{filepath_log=}\n\t{filepath_trace=}\n\t{filepath_scripts=}\n\t{filepath_dag=}\n\t{filepath_stdout=}\n\t{outfile=}")

if not os.path.isfile(filepath_log):
    print(f"ERROR: log file '{filepath_log}' does not exist!")
    quit()
if not os.path.isfile(filepath_trace):
    print(f"ERROR: trace file '{filepath_trace}' does not exist!")
    quit()
if not os.path.isfile(filepath_scripts):
    print(f"ERROR: scripts file '{filepath_scripts}' does not exist!")
    quit()
if not os.path.isfile(filepath_dag):
    print(f"ERROR: dag file '{filepath_dag}' does not exist!")
    quit()
if not os.path.isfile(filepath_stdout):
    print(f"ERROR: stdout file '{filepath_stdout}' does not exist!")
    quit()

#1. Parse files into various dictionaries (and list)
workflow = {}
parse_stdout(filepath_stdout, workflow)

processes   = []
task_id     = {}
realtime    = {}
pct_cpu     = {}
rss         = {}
rchar       = {}
wchar       = {}
read_bytes  = {}
write_bytes = {}
work_dir    = {}
parse_trace(filepath_trace, processes, task_id, realtime, pct_cpu, rss, rchar, wchar, read_bytes, write_bytes, work_dir)

scripts = {}
parse_scripts(filepath_scripts, scripts)

parents  = {}
children = {}
parse_dag(filepath_dag, parents, children)

files            = {}
file_bytes_read  = {}
file_bytes_write = {}
parse_log(filepath_log, processes, files, file_bytes_read, file_bytes_write)

#2. Create list of tasks for WfCommons JSON output
tasks = []
for i, process in enumerate(processes):
    curr_task                     = {}
    curr_task["name"]             = process
    curr_task["id"]               = task_id[process]
    curr_task["type"]             = "compute"
    curr_task["command"]          = scripts[process]                       #TODO: change to dictionary object?
    curr_task["parents"]          = parents[process]
    curr_task["children"]         = children[process]
    curr_task["files"]            = files[process]
    curr_task["runtime"]          = float(realtime[process])/1000.         #in seconds
    curr_task["avgCPU"]           = pct_cpu[process]
    curr_task["bytesRead"]        = float(rchar[process])/1000.            #in KB (not KiB!)
    curr_task["bytesWritten"]     = float(wchar[process])/1000.            #in KB (not KiB!)
    curr_task["inputFilesBytes"]  = float(file_bytes_read[process])
    curr_task["outputFilesBytes"] = float(file_bytes_write[process])
    curr_task["memory"]           = float(rss[process])/1000.              #in KB (not KiB!)

    tasks.append(curr_task)

workflow["tasks"] = tasks

#3. Create top level structures for WfCommons JSON output
wfcommons                  = {}
wfcommons["name"]          = workflow_name
wfcommons["description"]   = "Trace generated from Nextflow (via https://github.com/wfcommons/nextflow_workflow_tracer)"
wfcommons["createdAt"]     = str(datetime.now(tz=timezone.utc).isoformat())
wfcommons["schemaVersion"] = "1.3"

wms         = {}
wms["name"] = "Nextflow"
wms["url"]  = "https://www.nextflow.io/"

wfcommons["wms"]      = wms
wfcommons["workflow"] = workflow

wfcommons["machines"] = []

#Get machine info
single_machine = {}

completed_process = subprocess.run(["uname", "-n"], capture_output=True, encoding="utf-8")
single_machine["nodeName"] = str(completed_process.stdout).strip()

completed_process = subprocess.run(["uname", "-s"], capture_output=True, encoding="utf-8")
single_machine["system"] = str(completed_process.stdout).strip()

completed_process = subprocess.run(["uname", "-r"], capture_output=True, encoding="utf-8")
single_machine["release"] = str(completed_process.stdout).strip()

completed_process = subprocess.run(["uname", "-m"], capture_output=True, encoding="utf-8")
single_machine["architecture"] = str(completed_process.stdout).strip()

wfcommons["machines"].append(single_machine)


#4. Write JSON to output file
with open(outfile, "w") as fp:
    fp.write(json.dumps(wfcommons, indent=4))
