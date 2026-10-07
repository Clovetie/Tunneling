import json, math

data = json.load(open("poses.json"))
order = data["order"]
poses = data["poses"]

LABELS = {
    "idle":    ("IDLE",    "breathing, weight shifting"),
    "watch":   ("WATCH",   "head tracks the player"),
    "twitch":  ("TWITCH",  "random jerk, auto-returns"),
    "recoil":  ("RECOIL",  "flashed - shields its face"),
    "lunge":   ("LUNGE",   "breach - comes through"),
    "retreat": ("RETREAT", "folds down and away"),
}

def corners(p):
    px, py, pz = p["p"]; sx, sy, sz = p["s"]
    r = p["r"]
    hx, hy, hz = sx/2, sy/2, sz/2
    pts = []
    for dx in (-hx, hx):
        for dy in (-hy, hy):
            for dz in (-hz, hz):
                X = px + r[0]*dx + r[1]*dy + r[2]*dz
                Y = py + r[3]*dx + r[4]*dy + r[5]*dz
                Z = pz + r[6]*dx + r[7]*dy + r[8]*dz
                pts.append((X, Y, Z))
    return pts

def hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2: return pts
    def cross(o,a,b): return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lower=[]
    for p in pts:
        while len(lower)>=2 and cross(lower[-2],lower[-1],p)<=0: lower.pop()
        lower.append(p)
    upper=[]
    for p in reversed(pts):
        while len(upper)>=2 and cross(upper[-2],upper[-1],p)<=0: upper.pop()
        upper.append(p)
    return lower[:-1]+upper[:-1]

# view: "front" projects (X,Y) with Z as depth; "side" projects (-Z,Y) with X as depth
def render_pose(parts, view, w, h, pad=14):
    shapes = []
    for p in parts:
        cs = corners(p)
        if view == "front":
            pts2 = [(c[0], c[1]) for c in cs]; depth = sum(c[2] for c in cs)/8
        else:
            pts2 = [(-c[2], c[1]) for c in cs]; depth = -sum(c[0] for c in cs)/8
        shapes.append((depth, hull(pts2), p))

    allx = [x for _,poly,_ in shapes for x,_ in poly]
    ally = [y for _,poly,_ in shapes for _,y in poly]
    minx, maxx, miny, maxy = min(allx), max(allx), min(ally), max(ally)
    spanx, spany = maxx-minx, maxy-miny
    scale = min((w-2*pad)/max(spanx,0.01), (h-2*pad)/max(spany,0.01))
    ox = pad + (w-2*pad - spanx*scale)/2
    oy = pad + (h-2*pad - spany*scale)/2

    def tx(x,y):
        return (ox + (x-minx)*scale, h - (oy + (y-miny)*scale))

    shapes.sort(key=lambda s: s[0])
    out=[]
    for depth, poly, p in shapes:
        pts = " ".join(f"{tx(x,y)[0]:.1f},{tx(x,y)[1]:.1f}" for x,y in poly)
        r,g,b = p["c"]
        if p["neon"]:
            fill = "#f0d879"; stroke="#fff3c4"; sw=0.8; op=1.0
        else:
            # shade by depth so the silhouette reads
            t = 0.55 + 0.45*((depth - min(s[0] for s in shapes)) /
                             max(0.01, max(s[0] for s in shapes)-min(s[0] for s in shapes)))
            fill = "#%02x%02x%02x" % (int(r*t*0.75), int(g*t*0.75), int(b*t*0.78))
            stroke = "#%02x%02x%02x" % (min(255,int(r*0.95)), min(255,int(g*0.95)), min(255,int(b*0.95)))
            sw=0.55; op=0.97
        out.append(f'<polygon points="{pts}" fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"/>')
    return "\n".join(out)

CW, CH = 250, 330
cols, rows = 6, 2
W = cols*CW + 40
H = rows*CH + 112

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
svg.append(f'<rect width="{W}" height="{H}" fill="#0b0c10"/>')
svg.append('<defs><filter id="g"><feGaussianBlur stdDeviation="2.2" result="b"/>'
           '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')
svg.append(f'<text x="20" y="34" fill="#e8e3d6" font-family="Georgia,serif" font-size="21">'
           f'The Pale Watcher &#183; procedural pose sheet</text>')
svg.append(f'<text x="20" y="54" fill="#6f6a60" font-family="Georgia,serif" font-size="12">'
           f'27 parts &#183; 26 Motor6D joints &#183; 9.85 studs tall &#183; rendered from live rig geometry, not concept art</text>')

for i, name in enumerate(order):
    x0 = 20 + i*CW
    title, sub = LABELS[name]
    for row, view in enumerate(("front","side")):
        y0 = 72 + row*CH
        svg.append(f'<g transform="translate({x0},{y0})">')
        svg.append(f'<rect width="{CW-10}" height="{CH-12}" fill="#121319" stroke="#23242c" rx="4"/>')
        svg.append(render_pose(poses[name], view, CW-10, CH-12))
        if row == 0:
            svg.append(f'<text x="10" y="20" fill="#d8d2c4" font-family="Georgia,serif" font-size="14" letter-spacing="1.5">{title}</text>')
            svg.append(f'<text x="10" y="36" fill="#6f6a60" font-family="Georgia,serif" font-size="10">{sub}</text>')
        else:
            svg.append(f'<text x="10" y="20" fill="#4a4740" font-family="Georgia,serif" font-size="10">side</text>')
        svg.append('</g>')

svg.append('</svg>')
open("nightloop/monster-posesheet.svg","w").write("\n".join(svg))
print("wrote nightloop/monster-posesheet.svg")
