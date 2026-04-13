# app.py
import streamlit as st
import numpy as np
import random
import matplotlib.pyplot as plt
import requests
import time
from io import BytesIO

st.set_page_config(page_title='Carbon-Aware Cloud Scheduler', layout='wide')

# ----------------------------
# YOUR RENDER SERVER CARBON ENDPOINTS (must return JSON with "carbon" or "carbon_intensity")
# ----------------------------
RENDER_SERVERS = [
    {"name": "Frankfurt (EU)", "url": "https://s1-gicc.onrender.com/carbon"},
    {"name": "oregon",         "url": "https://s3-gboc.onrender.com/carbon"},
    {"name": "Singapore ",     "url": "https://s4-kzld.onrender.com/carbon"}
]

# ----------------------------
# Helpers: fetch live carbon safely
# ----------------------------
def fetch_carbon(url):
    try:
        r = requests.get(url, timeout=4)
        r.raise_for_status()
        j = r.json()
        # support multiple key names used above examples
        return float(j.get("carbon_intensity") or j.get("carbon") or j.get("ci") or j.get("value"))
    except Exception:
        return None

def build_servers(default_cpu=16, default_ram=64, fallback_ci=250):
    servers = []
    for i, s in enumerate(RENDER_SERVERS):
        ci = fetch_carbon(s["url"])
        servers.append({
            "id": i,
            "name": s["name"],
            "cpu": default_cpu,
            "ram": default_ram,
            "carbon_intensity": ci if ci is not None else fallback_ci,
            "ci_source": "live" if ci is not None else "fallback"
        })
        time.sleep(0.15)
    return servers

# ----------------------------
# Task generator
# ----------------------------
def generate_tasks(n, cpu_range, ram_range, dur_range, deadline_range):
    tasks = []
    for i in range(n):
        tasks.append({
            "id": i,
            "cpu_req": random.randint(*cpu_range),
            "ram_req": random.randint(*ram_range),
            "duration": random.randint(*dur_range),    # hours
            "deadline": random.randint(*deadline_range)
        })
    return tasks

# ----------------------------
# Fitness & GA
# ----------------------------
def compute_carbon(chrom, tasks, servers):
    total = 0.0
    cap_cpu = [0]*len(servers)
    cap_ram = [0]*len(servers)
    for t_idx, s_id in enumerate(chrom):
        t = tasks[t_idx]
        srv = servers[s_id]
        cap_cpu[s_id] += t["cpu_req"]
        cap_ram[s_id] += t["ram_req"]
        # energy estimate ~ duration * cpu_req * (ci/1000) -> kgCO2
        total += t["duration"] * t["cpu_req"] * (srv["carbon_intensity"] / 1000.0)
    # capacity penalties (heavy)
    penalty = 0.0
    for j, srv in enumerate(servers):
        if cap_cpu[j] > srv["cpu"]:
            penalty += (cap_cpu[j]-srv["cpu"]) * 500.0
        if cap_ram[j] > srv["ram"]:
            penalty += (cap_ram[j]-srv["ram"]) * 300.0
    return total + penalty

def init_population(pop_size, n_tasks, n_servers):
    return [[random.randrange(n_servers) for _ in range(n_tasks)] for _ in range(pop_size)]

def tournament_select(pop, fitness):
    a,b = random.randrange(len(pop)), random.randrange(len(pop))
    return pop[a] if fitness[a] < fitness[b] else pop[b]

def crossover(a,b):
    if len(a) < 3: return a[:], b[:]
    p = random.randint(1, len(a)-2)
    return a[:p]+b[p:], b[:p]+a[p:]

def mutate(ch, n_servers, rate=0.08):
    for i in range(len(ch)):
        if random.random() < rate:
            ch[i] = random.randrange(n_servers)
    return ch

def run_GA(tasks, servers, pop_size=40, generations=120, mut_rate=0.12, callback=None):
    n_tasks = len(tasks); n_servers = len(servers)
    pop = init_population(pop_size, n_tasks, n_servers)
    best_progress = []
    for gen in range(generations):
        fitness = [compute_carbon(ind, tasks, servers) for ind in pop]
        new_pop = []
        for _ in range(pop_size//2):
            p1 = tournament_select(pop, fitness)
            p2 = tournament_select(pop, fitness)
            c1, c2 = crossover(p1, p2)
            new_pop.append(mutate(c1, n_servers, mut_rate))
            new_pop.append(mutate(c2, n_servers, mut_rate))
        pop = new_pop
        fit_vals = [compute_carbon(ind, tasks, servers) for ind in pop]
        best = min(fit_vals)
        best_progress.append(best)
        if callback and gen % max(1, generations//10) == 0:
            callback(gen, generations, best)
    final_fitness = [compute_carbon(ind, tasks, servers) for ind in pop]
    best_idx = int(np.argmin(final_fitness))
    return pop[best_idx], best_progress

# ----------------------------
# Visuals
# ----------------------------
def plot_and_get_image(best, progress, tasks, servers):
    fig, axes = plt.subplots(2,2, figsize=(12,8))
    ax1,ax2,ax3,ax4 = axes.ravel()
    ax1.plot(progress, marker='o'); ax1.set_title("GA Convergence (fitness kgCO2)")
    count = [0]*len(servers)
    for s in best: count[s]+=1
    ax2.bar([s["name"] for s in servers], count); ax2.set_title("Task distribution")
    ax3.barh([s["name"] for s in servers], [s["carbon_intensity"] for s in servers]); ax3.set_title("Carbon intensity (gCO2/kWh)")
    used = [0]*len(servers)
    for tid,sid in enumerate(best): used[sid]+=tasks[tid]["cpu_req"]
    caps = [s["cpu"] for s in servers]; x = np.arange(len(servers)); w=0.35
    ax4.bar(x-w/2, used, w, label='Used'); ax4.bar(x+w/2, caps, w, label='Capacity'); ax4.set_xticks(x); ax4.set_xticklabels([s["name"] for s in servers]); ax4.legend(); ax4.set_title("CPU usage vs capacity")
    plt.tight_layout()
    buf = BytesIO(); plt.savefig(buf, format='png'); buf.seek(0)
    return buf

# ----------------------------
# Streamlit UI & flow
# ----------------------------
st.title("🌱 Carbon-Aware Cloud Task Scheduler (Real servers)")
st.write("Uses live carbon from your Render servers; GA minimizes total kgCO₂ while respecting capacity.")

with st.sidebar:
    n_tasks = st.slider("Number of tasks", 5,50,12)
    cpu_min = st.number_input("Task CPU min", 1,16,1)
    cpu_max = st.number_input("Task CPU max", 1,16,8)
    ram_min = st.number_input("Task RAM min (GB)", 1,64,2)
    ram_max = st.number_input("Task RAM max (GB)", 1,64,8)
    dur_min = st.number_input("Task duration min (hrs)", 1,24,1)
    dur_max = st.number_input("Task duration max (hrs)", 1,24,4)
    pop_size = st.slider("GA population", 20,100,40)
    generations = st.slider("GA generations", 20,300,120)
    mutation_rate = st.slider("Mutation rate", 0.01,0.5,0.12)

if st.button("Run scheduler (fetch live CI)"):
    st.info("Fetching carbon intensity from Render servers...")
    servers = build_servers()
    st.write("**Servers (live CI / source)**")
    for s in servers:
        st.write(f"- {s['name']}: {s['carbon_intensity']} gCO₂/kWh  ({s['ci_source']})")

    tasks = generate_tasks(n_tasks, (cpu_min,cpu_max), (ram_min,ram_max), (dur_min,dur_max), (1,24))
    progress_bar = st.progress(0)

    def cb(g, G, best):
        progress_bar.progress(int((g+1)/G * 100))
        st.write(f"gen {g+1}/{G}  best fitness: {best:.4f}")

    best_solution, progress = run_GA(tasks, servers, pop_size, generations, mutation_rate, callback=None)
    img_buf = plot_and_get_image(best_solution, progress, tasks, servers)
    st.image(img_buf)

    st.subheader("Task → Server assignment")
    total_em = 0.0
    for tid, sid in enumerate(best_solution):
        srv = servers[sid]
        t = tasks[tid]
        est = t["duration"] * t["cpu_req"] * (srv["carbon_intensity"]/1000.0)
        total_em += est
        st.write(f"Task {tid+1}: CPU {t['cpu_req']} RAM {t['ram_req']} dur {t['duration']}h  →  {srv['name']} (CI {srv['carbon_intensity']})  est_kgCO₂: {est:.4f}")

    st.success(f"Estimated total emissions (kgCO₂): {total_em:.4f}")
