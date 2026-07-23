from __future__ import annotations

import copy, io, json, math, queue, re, subprocess, threading, tkinter as tk, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import font as tkfont, simpledialog
from clicker_tool import ClickerTheme

try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None

TILE = 256
MAX_LAT = 85.05112878
UA = "PasserMap/1.0 (desktop utility)"

def to_world(lat, lon, zoom):
    lat = max(-MAX_LAT, min(MAX_LAT, lat)); size = TILE * (1 << zoom)
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360 * size, (0.5 - math.log((1+s)/(1-s))/(4*math.pi)) * size

def to_geo(x, y, zoom):
    size = TILE * (1 << zoom); n = math.pi - 2 * math.pi * y / size
    return max(-MAX_LAT, min(MAX_LAT, math.degrees(math.atan(math.sinh(n))))), ((x/size*360) % 360) - 180

class MapWindow:
    WIDTH, HEIGHT = 1800, 1300
    MIN_WIDTH, MIN_HEIGHT = 700, 500
    def __init__(self, app, theme: ClickerTheme):
        self.app, self.theme, self.closed = app, theme, False
        self.center_lat, self.center_lon, self.zoom = 39.9042, 116.4074, 10
        self.data_dir = Path(app.store_dir).parent
        self.state_file = self.data_dir / "map_state.json"
        self.state_backup_file = self.state_file.with_suffix(".json.bak")
        self.notes = []
        self.info_data = None
        self.context_point = None
        self.undo_stack = []
        self.marker = self.move_start = self.resize_start = self.drag_start = self.drag_world = None
        self.restored_query = ""
        self.restored_window_size = (self.WIDTH, self.HEIGHT)
        self._state_recovered_from_backup = False
        self._last_saved_payload = ""
        self.load_state()
        self.mode = None
        self.action_points = []
        self.route_points = []
        self.measure_points = []
        self.notice = ""
        self.pending, self.photos, self.render_id = set(), {}, None
        self.tile_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="Passer-MapTile")
        self.tile_futures = {}
        self.tile_results = queue.Queue()
        self.tile_poll_id = None
        self.inspect_seq = 0
        self.cache = self.data_dir / "MapTiles"; self.cache.mkdir(parents=True, exist_ok=True)
        self.query = tk.StringVar(value=self.restored_query); self.status = tk.StringVar(value="拖动地图，滚轮缩放；可搜索地点或输入“纬度, 经度”")
        self.window = tk.Toplevel(app.root); self.window.withdraw(); self.window.overrideredirect(True)
        self.window.configure(bg=theme.border); self.window.resizable(False, False)
        restored_width,restored_height=self.restored_window_size
        self.window.geometry(f"{restored_width}x{restored_height}")
        self.shell = tk.Frame(self.window, bg=theme.surface_bg, highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self.build_chrome(); self.build_toolbar()
        self.canvas = tk.Canvas(self.shell, bg="#dbe5ec", bd=0, highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self.schedule())
        self.canvas.bind("<ButtonPress-1>", self.map_press); self.canvas.bind("<B1-Motion>", self.map_drag)
        self.canvas.bind("<ButtonRelease-1>", self.map_release)
        self.canvas.bind("<MouseWheel>", lambda e: self.zoom_by(1 if e.delta > 0 else -1, e.x, e.y))
        self.canvas.bind("<Double-Button-1>", lambda e: self.zoom_by(1, e.x, e.y))
        self.canvas.bind("<Button-3>", self.show_map_menu)
        self.map_menu=tk.Menu(self.window,tearoff=False)
        self.map_menu.add_command(label="添加备注",command=self.add_note)
        self.map_menu.add_command(label="添加计划",command=self.add_plan_here)
        self.map_menu.add_command(label="添加到Passer",command=self.add_to_passer)
        self.info_bubble=tk.Text(self.canvas,wrap=tk.WORD,bg="#ffffff",fg="#0f172a",
                                 selectbackground="#bfdbfe",selectforeground="#0f172a",
                                 relief=tk.SOLID,bd=1,padx=11,pady=9,cursor="xterm",
                                 font=self.font(10,"bold"),takefocus=True)
        self.info_bubble.configure(state=tk.DISABLED)
        self.info_bubble.bind("<Control-a>",self.select_all_info)
        self.info_bubble.bind("<Control-A>",self.select_all_info)
        bottom=tk.Frame(self.shell,bg=theme.title_bg,height=24)
        bottom.pack(side=tk.BOTTOM,fill=tk.X);bottom.pack_propagate(False)
        tk.Label(bottom,textvariable=self.status,bg=theme.title_bg,fg="#cbd5e1",anchor=tk.W,
                 padx=10,font=self.font(8)).pack(side=tk.LEFT,fill=tk.X,expand=True)
        self.resize_grip=tk.Label(bottom,text="◢",bg=theme.title_bg,fg="#8aa0c0",
                                  cursor="size_nw_se",font=self.font(11,"bold"))
        self.resize_grip.pack(side=tk.RIGHT,padx=(4,6))
        self.resize_grip.bind("<ButtonPress-1>",self.start_resize)
        self.resize_grip.bind("<B1-Motion>",self.do_resize)
        self.resize_grip.bind("<ButtonRelease-1>",lambda _e:self.save_state())
        self.window.bind("<Escape>", lambda e: self.close()); self.window.protocol("WM_DELETE_WINDOW", self.close)
        try:
            x,y=theme.center_over_root(app.root,restored_width,restored_height); theme.place_toplevel_absolute(self.window,restored_width,restored_height,x,y)
        except Exception: pass
        self.window.attributes("-topmost", app.topmost_var.get()); app.apply_window_transparency(self.window)
        self.window.deiconify(); self.window.focus_force(); self.schedule()
        self.tile_poll_id=self.window.after(30,self.poll_tiles)
        if self._state_recovered_from_backup:
            self.notice="主地图状态损坏，已从备份恢复。";self.status.set(self.notice)
            self.window.after_idle(self.save_state)

    def font(self, size=9, weight="normal"): return self.theme.app_font(size, weight)
    @staticmethod
    def _valid_coord(lat,lon):
        try:
            lat,lon=float(lat),float(lon)
            return math.isfinite(lat) and math.isfinite(lon) and -MAX_LAT<=lat<=MAX_LAT
        except (TypeError,ValueError):return False
    @staticmethod
    def _normal_lon(value):return ((float(value)+180.0)%360.0)-180.0
    def _clean_notes(self,notes):
        cleaned=[]
        for note in notes if isinstance(notes,list) else []:
            if not isinstance(note,dict) or not self._valid_coord(note.get("lat"),note.get("lon")):continue
            text=str(note.get("text") or "").strip()[:500]
            if not text:continue
            cleaned.append({"lat":float(note["lat"]),"lon":self._normal_lon(note["lon"]),"text":text})
            if len(cleaned)>=500:break
        return cleaned
    def _apply_state_data(self,data):
        if not isinstance(data,dict):raise ValueError("地图状态不是对象")
        center=data.get("center") or []
        if isinstance(center,(list,tuple)) and len(center)==2 and self._valid_coord(center[0],center[1]):
            self.center_lat=float(center[0]);self.center_lon=self._normal_lon(center[1])
        try:self.zoom=max(2,min(19,int(data.get("zoom",self.zoom))))
        except (TypeError,ValueError):pass
        self.notes=self._clean_notes(data.get("notes",[]))
        selection=data.get("selection")
        if isinstance(selection,dict) and self._valid_coord(selection.get("lat"),selection.get("lon")):
            lat=float(selection["lat"]);lon=self._normal_lon(selection["lon"])
            label=str(selection.get("label") or "选中位置").strip()[:120] or "选中位置"
            self.marker=(lat,lon,label[:30])
            raw_info=selection.get("info") if isinstance(selection.get("info"),dict) else {}
            self.info_data={
                "lat":lat,"lon":lon,"title":str(raw_info.get("title") or label).strip()[:200] or label,
                "address":str(raw_info.get("address") or label).strip()[:1000] or label,
            }
            note=str(raw_info.get("note") or "").strip()[:500]
            if note:self.info_data["note"]=note
        self.restored_query=str(data.get("query") or "").strip()[:500]
        size=data.get("window_size") or []
        if isinstance(size,(list,tuple)) and len(size)==2:
            try:
                width=max(self.MIN_WIDTH,min(3200,int(size[0])));height=max(self.MIN_HEIGHT,min(2200,int(size[1])))
                self.restored_window_size=(width,height)
            except (TypeError,ValueError):pass
    def load_state(self):
        for index,path in enumerate((self.state_file,self.state_backup_file)):
            try:
                self._apply_state_data(json.loads(path.read_text(encoding="utf-8")))
                self._state_recovered_from_backup=index==1
                return True
            except (OSError,UnicodeError,json.JSONDecodeError,TypeError,ValueError):continue
        return False
    def _state_payload(self):
        selection=None
        if self.marker and self._valid_coord(self.marker[0],self.marker[1]):
            info=self.info_data if isinstance(self.info_data,dict) else {}
            selection={
                "lat":float(self.marker[0]),"lon":self._normal_lon(self.marker[1]),
                "label":str(self.marker[2] or "选中位置")[:120],
                "info":{
                    "title":str(info.get("title") or self.marker[2] or "选中位置")[:200],
                    "address":str(info.get("address") or "")[:1000],
                    "note":str(info.get("note") or "")[:500],
                },
            }
        try:query=str(self.query.get()).strip()[:500]
        except Exception:query=self.restored_query
        try:size=[max(self.MIN_WIDTH,int(self.window.winfo_width())),max(self.MIN_HEIGHT,int(self.window.winfo_height()))]
        except Exception:size=list(self.restored_window_size)
        return {"version":2,"center":[float(self.center_lat),self._normal_lon(self.center_lon)],"zoom":max(2,min(19,int(self.zoom))),
                "notes":self._clean_notes(self.notes),"selection":selection,"query":query,"window_size":size}
    def save_state(self):
        temporary=self.state_file.with_suffix(".json.tmp")
        try:
            self.state_file.parent.mkdir(parents=True,exist_ok=True)
            payload=json.dumps(self._state_payload(),ensure_ascii=False,indent=2)
            if payload==self._last_saved_payload and not self._state_recovered_from_backup:return True
            temporary.write_text(payload,encoding="utf-8")
            if self.state_file.exists() and not self._state_recovered_from_backup:
                try:self.state_backup_file.write_bytes(self.state_file.read_bytes())
                except OSError:pass
            temporary.replace(self.state_file)
            self._last_saved_payload=payload;self._state_recovered_from_backup=False
            return True
        except (OSError,UnicodeError,TypeError,ValueError):
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
            return False
    def build_chrome(self):
        bar=tk.Frame(self.shell,bg=self.theme.title_bg,height=46); bar.pack(side=tk.TOP,fill=tk.X); bar.pack_propagate(False)
        title=tk.Label(bar,text=self.theme.title,bg=self.theme.title_bg,fg="#dbe7ff",anchor=tk.W,font=self.font(10,"bold"))
        title.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(14,8))
        b=tk.Button(bar,text="×",command=self.close,bd=0,padx=11,pady=5,bg=self.theme.title_button_bg,fg="#e7eefc",
                    activebackground="#ef4444",activeforeground="white",cursor="hand2",font=self.font(12))
        b.pack(side=tk.RIGHT,padx=(0,8),pady=8); b.bind("<Enter>",lambda e:b.configure(bg="#ef4444")); b.bind("<Leave>",lambda e:b.configure(bg=self.theme.title_button_bg))
        for w in (bar,title): w.bind("<ButtonPress-1>",self.start_move); w.bind("<B1-Motion>",self.do_move)
    def build_toolbar(self):
        f=tk.Frame(self.shell,bg=self.theme.surface_bg,height=52); f.pack(side=tk.TOP,fill=tk.X); f.pack_propagate(False)
        e=tk.Entry(f,textvariable=self.query,bd=0,bg="#f8fafc",fg="#111827",insertbackground="#111827",
                   highlightthickness=1,highlightbackground=self.theme.border,highlightcolor=self.theme.accent,font=self.font(10))
        e.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(12,6),pady=10,ipady=6)
        self.search_entry=e
        e.bind("<Return>",lambda x:self._search_enter())
        e.bind("<KeyRelease>",self._on_query_key)
        e.bind("<Down>",self._focus_suggestions)
        e.bind("<Escape>",lambda x:self.hide_suggestions())
        self.button(f,"搜索",self.search,True).pack(side=tk.LEFT,padx=4,pady=10)
        self.button(f,"保存地点",self.save_current_location).pack(side=tk.LEFT,padx=(6,2),pady=10)
        self.load_location_button=self.button(f,"加载地点",self.show_saved_locations)
        self.load_location_button.pack(side=tk.LEFT,padx=2,pady=10)
        self.saved_locations_menu=tk.Menu(self.window,tearoff=False)
        self.button(f,"定位",self.locate).pack(side=tk.LEFT,padx=(6,2),pady=10)
        self.button(f,"路线",lambda:self.set_mode("route")).pack(side=tk.LEFT,padx=2,pady=10)
        self.button(f,"测距",lambda:self.set_mode("measure")).pack(side=tk.LEFT,padx=2,pady=10)
        self.button(f,"−",lambda:self.zoom_by(-1)).pack(side=tk.LEFT,padx=(6,2),pady=10)
        self.button(f,"+",lambda:self.zoom_by(1)).pack(side=tk.LEFT,padx=2,pady=10)
    def button(self,p,text,cmd,primary=False):
        return tk.Button(p,text=text,command=cmd,bd=0,padx=12,pady=5,bg=self.theme.accent if primary else "#eef2f9",
                         fg="white" if primary else "#1f2937",activebackground=self.theme.accent_hover if primary else "#e2e8f4",
                         activeforeground="white" if primary else "#111827",cursor="hand2",font=self.font(9,"bold" if primary else "normal"))
    def start_move(self,e): self.move_start=(e.x_root,e.y_root,self.window.winfo_x(),self.window.winfo_y())
    def do_move(self,e):
        if self.move_start:
            sx,sy,x,y=self.move_start; self.window.geometry(f"+{x+e.x_root-sx}+{y+e.y_root-sy}")
    def start_resize(self,e):
        self.resize_start=(e.x_root,e.y_root,self.window.winfo_width(),self.window.winfo_height())
    def do_resize(self,e):
        if not self.resize_start:return
        sx,sy,sw,sh=self.resize_start
        width=max(self.MIN_WIDTH,sw+e.x_root-sx);height=max(self.MIN_HEIGHT,sh+e.y_root-sy)
        self.window.geometry(f"{width}x{height}")
    def map_press(self,e):
        self.hide_suggestions()
        self._push_undo();self.drag_start=(e.x,e.y);self.drag_world=to_world(self.center_lat,self.center_lon,self.zoom)
    def map_drag(self,e):
        if self.drag_start and self.drag_world:
            self.center_lat,self.center_lon=to_geo(self.drag_world[0]-e.x+self.drag_start[0],self.drag_world[1]-e.y+self.drag_start[1],self.zoom); self.schedule(33)
    def map_release(self,e):
        start=self.drag_start; self.drag_start=self.drag_world=None
        if not start:return
        if abs(e.x-start[0])>4 or abs(e.y-start[1])>4:
            self.save_state();return
        cx,cy=to_world(self.center_lat,self.center_lon,self.zoom)
        lat,lon=to_geo(cx+e.x-self.canvas.winfo_width()/2,cy+e.y-self.canvas.winfo_height()/2,self.zoom)
        if not self.mode:
            self.inspect_point(lat,lon);return
        if self.mode=="measure":
            self.measure_points.append((lat,lon)); self.schedule()
            self.notice=self.measure_summary(); self.status.set(self.notice); return
        self.action_points.append((lat,lon)); self.schedule()
        if len(self.action_points)<2:
            self.notice="已选择起点，请点击终点。"
            self.status.set(self.notice); return
        self.mode=None; self.request_route()
    def zoom_by(self,d,x=None,y=None):
        nz=max(2,min(19,self.zoom+d))
        if nz==self.zoom:return
        self._push_undo()
        w,h=max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height()); x=w/2 if x is None else x; y=h/2 if y is None else y
        cx,cy=to_world(self.center_lat,self.center_lon,self.zoom); lat,lon=to_geo(cx+x-w/2,cy+y-h/2,self.zoom)
        self.zoom=nz; ax,ay=to_world(lat,lon,nz); self.center_lat,self.center_lon=to_geo(ax-x+w/2,ay-y+h/2,nz); self.schedule()
        self.save_state()
    def home(self): self._push_undo();self.center_lat,self.center_lon,self.zoom,self.marker=39.9042,116.4074,10,None; self.info_data=None;self.query.set("");self.clear_actions();self.save_state();self.schedule()
    def schedule(self,delay=16):
        if self.closed:return
        if self.render_id is None:self.render_id=self.window.after(delay,self.render)
    def render(self):
        self.render_id=None
        if self.closed or ImageTk is None:
            if ImageTk is None:self.status.set("地图需要 Pillow 图像组件。")
            return
        c=self.canvas; w,h=max(1,c.winfo_width()),max(1,c.winfo_height()); c.delete("all")
        cx,cy=to_world(self.center_lat,self.center_lon,self.zoom); left,top=cx-w/2,cy-h/2; n=1<<self.zoom; visible=set()
        for ty in range(math.floor(top/TILE),math.floor((top+h)/TILE)+1):
            if not 0<=ty<n:continue
            for rawx in range(math.floor(left/TILE),math.floor((left+w)/TILE)+1):
                tx=rawx%n; key=(self.zoom,tx,ty); visible.add(key); sx,sy=rawx*TILE-left,ty*TILE-top
                tag=f"tile-{self.zoom}-{rawx}-{ty}"
                if key in self.photos:c.create_image(sx,sy,image=self.photos[key],anchor=tk.NW,tags=(tag,))
                else:c.create_rectangle(sx,sy,sx+TILE,sy+TILE,fill="#e5edf2",outline="#cbd5e1",tags=(tag,));self.request_tile(key)
        if self.marker:
            mx,my=to_world(self.marker[0],self.marker[1],self.zoom); sx,sy=mx-left,my-top
            c.create_oval(sx-8,sy-8,sx+8,sy+8,fill="#ef4444",outline="white",width=3)
            c.create_text(sx+12,sy-12,text=self.marker[2],anchor=tk.SW,fill="#111827",font=self.font(9,"bold"))
        if self.route_points:
            coords=[]
            for lat,lon in self.route_points:
                px,py=to_world(lat,lon,self.zoom); coords.extend((px-left,py-top))
            if len(coords)>=4:
                c.create_line(*coords,fill=self.theme.accent,width=6,smooth=True)
                c.create_line(*coords,fill=self.theme.accent_soft_hover,width=3,smooth=True)
        for i,(lat,lon) in enumerate(self.action_points[:2]):
            px,py=to_world(lat,lon,self.zoom); sx,sy=px-left,py-top
            color="#16a34a" if i==0 else "#ef4444"
            c.create_oval(sx-9,sy-9,sx+9,sy+9,fill=color,outline="white",width=3)
            c.create_text(sx,sy,text="A" if i==0 else "B",fill="white",font=self.font(8,"bold"))
        if self.measure_points:
            coords=[]
            for lat,lon in self.measure_points:
                px,py=to_world(lat,lon,self.zoom); coords.extend((px-left,py-top))
            if len(coords)>=4: c.create_line(*coords,fill="#f97316",width=4)
            for i,(lat,lon) in enumerate(self.measure_points):
                px,py=to_world(lat,lon,self.zoom); sx,sy=px-left,py-top
                c.create_oval(sx-6,sy-6,sx+6,sy+6,fill="#f97316",outline="white",width=2)
                c.create_text(sx,sy-13,text=str(i+1),fill="#9a3412",font=self.font(8,"bold"))
            if len(self.measure_points)>=2:
                lat,lon=self.measure_points[-1]; px,py=to_world(lat,lon,self.zoom); sx,sy=px-left,py-top
                c.create_text(sx+10,sy+8,text=self.format_distance(self.measure_total()),anchor=tk.NW,fill="#9a3412",font=self.font(9,"bold"))
        for note in self.notes:
            px,py=to_world(float(note["lat"]),float(note["lon"]),self.zoom);sx,sy=px-left,py-top
            if -20<=sx<=w+20 and -20<=sy<=h+20:
                c.create_oval(sx-7,sy-7,sx+7,sy+7,fill="#f59e0b",outline="white",width=2)
                c.create_text(sx,sy-12,text="备注",anchor=tk.S,fill="#92400e",font=self.font(8,"bold"))
        self.draw_info_bubble()
        c.create_text(w-8,h-6,text="© OpenStreetMap contributors",anchor=tk.SE,fill="#334155",font=self.font(8))
        if not self.notice:self.status.set(f"缩放 {self.zoom}　中心 {self.center_lat:.5f}, {self.center_lon:.5f}")
        if len(self.photos)>180:self.photos={k:v for k,v in self.photos.items() if k in visible}
        self.cancel_stale_tiles(visible)
    def tile_path(self,key):z,x,y=key;return self.cache/str(z)/str(x)/f"{y}.png"
    def request_tile(self,key):
        if key in self.pending:return
        p=self.tile_path(key)
        self.pending.add(key)
        self.tile_futures[key]=self.tile_pool.submit(self.load_tile,key,p)
    def load_tile(self,key,p):
        image=None
        try:
            if p.exists():data=p.read_bytes()
            else:
                z,x,y=key; req=urllib.request.Request(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",headers={"User-Agent":UA})
                with urllib.request.urlopen(req,timeout=12) as r:data=r.read()
                p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            image=Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:pass
        self.tile_results.put((key,image))
    def tile_done(self,key,image):
        self.pending.discard(key)
        self.tile_futures.pop(key,None)
        if image is not None and not self.closed:
            try:self.photos[key]=ImageTk.PhotoImage(image);self.draw_loaded_tile(key)
            except Exception:pass
    def poll_tiles(self):
        self.tile_poll_id=None
        if self.closed:return
        for _ in range(12):
            try:key,image=self.tile_results.get_nowait()
            except queue.Empty:break
            self.tile_done(key,image)
        self.tile_poll_id=self.window.after(30,self.poll_tiles)
    def draw_loaded_tile(self,key):
        if key[0]!=self.zoom or key not in self.photos:return
        c=self.canvas;w,h=max(1,c.winfo_width()),max(1,c.winfo_height())
        cx,cy=to_world(self.center_lat,self.center_lon,self.zoom);left,top=cx-w/2,cy-h/2;n=1<<self.zoom
        z,x,y=key
        firstx=math.floor(left/TILE);lastx=math.floor((left+w)/TILE)
        for rawx in range(firstx,lastx+1):
            if rawx%n!=x:continue
            sx,sy=rawx*TILE-left,y*TILE-top
            if sx>=w or sy>=h or sx+TILE<=0 or sy+TILE<=0:continue
            tag=f"tile-{z}-{rawx}-{y}";c.delete(tag)
            c.create_image(sx,sy,image=self.photos[key],anchor=tk.NW,tags=(tag,));c.tag_lower(tag)
    def cancel_stale_tiles(self,visible):
        for key,future in list(self.tile_futures.items()):
            if key not in visible and future.cancel():
                self.tile_futures.pop(key,None);self.pending.discard(key)
    def accept(self,key,data):
        try:self.photos[key]=ImageTk.PhotoImage(Image.open(io.BytesIO(data)).convert("RGB"));self.draw_loaded_tile(key)
        except Exception:pass
    def search(self):
        q=self.query.get().strip()
        if not q:return
        m=re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[,， ]\s*(-?\d+(?:\.\d+)?)\s*",q)
        if m:
            lat,lon=map(float,m.groups())
            if -90<=lat<=90 and -180<=lon<=180:self.go(lat,lon,q);return
        self.status.set("正在搜索地点…")
        def work():
            result=None
            try:
                qs=urllib.parse.urlencode({"q":q,"format":"jsonv2","limit":1});req=urllib.request.Request("https://nominatim.openstreetmap.org/search?"+qs,headers={"User-Agent":UA})
                with urllib.request.urlopen(req,timeout=15) as r:rows=json.loads(r.read().decode())
                if rows:result=(float(rows[0]["lat"]),float(rows[0]["lon"]),str(rows[0].get("display_name") or q))
            except Exception:pass
            try:self.window.after(0,lambda:self.search_done(result))
            except Exception:pass
        threading.Thread(target=work,daemon=True,name="Passer-MapSearch").start()
    def search_done(self,r):
        if r:self.go(*r)
        elif not self.closed:self.status.set("未找到地点，或当前网络不可用。也可直接输入纬度, 经度。")

    # -- 实时联想搜索（类似 Passer 搜索框）-------------------------------
    def _search_enter(self):
        self.hide_suggestions(); self.search()

    def _on_query_key(self,e):
        if e.keysym in ("Up","Down","Return","Escape","Tab"): return
        if getattr(self,"_suggest_after",None):
            try:self.window.after_cancel(self._suggest_after)
            except Exception:pass
        self._suggest_after=self.window.after(140,self.fetch_suggestions)

    def fetch_suggestions(self):
        self._suggest_after=None
        if self.closed:return
        q=self.query.get().strip()
        if len(q)<1 or re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*[,， ]\s*-?\d+(?:\.\d+)?\s*",q):
            self.hide_suggestions();return
        self._suggest_seq=getattr(self,"_suggest_seq",0)+1; seq=self._suggest_seq
        def work():
            rows=[]
            try:
                qs=urllib.parse.urlencode({"q":q,"format":"jsonv2","limit":7})
                req=urllib.request.Request("https://nominatim.openstreetmap.org/search?"+qs,headers={"User-Agent":UA})
                with urllib.request.urlopen(req,timeout=12) as r:data=json.loads(r.read().decode())
                rows=[(str(x.get("display_name") or ""),float(x["lat"]),float(x["lon"])) for x in data if x.get("lat")]
            except Exception:pass
            try:self.window.after(0,lambda:self._suggest_done(seq,rows))
            except Exception:pass
        threading.Thread(target=work,daemon=True,name="Passer-MapSuggest").start()

    def _suggest_done(self,seq,rows):
        if self.closed or seq!=getattr(self,"_suggest_seq",0):return
        if not rows or not self.query.get().strip():
            self.hide_suggestions();return
        self.show_suggestions(rows)

    def _ensure_suggest_box(self):
        if getattr(self,"suggest_frame",None):return
        self.suggest_frame=tk.Frame(self.shell,bg=self.theme.border)
        self.suggest_list=tk.Listbox(self.suggest_frame,bd=0,bg="white",fg="#111827",
            highlightthickness=0,activestyle="none",selectbackground=self.theme.accent_soft,selectforeground="#111827",
            font=self.font(9),height=6)
        self.suggest_list.pack(fill=tk.BOTH,expand=True,padx=1,pady=1)
        self.suggest_list.bind("<Return>",lambda e:self._use_suggestion())
        self.suggest_list.bind("<Double-Button-1>",lambda e:self._use_suggestion())
        self.suggest_list.bind("<Button-1>",self._suggest_click)
        self.suggest_list.bind("<Escape>",lambda e:(self.hide_suggestions(),self.search_entry.focus_set()))

    def show_suggestions(self,rows):
        self._ensure_suggest_box()
        self.suggest_rows=rows
        lb=self.suggest_list; lb.delete(0,tk.END)
        for name,lat,lon in rows:
            short=name.split(",")[0].strip(); rest=name[len(short):].strip(", ")
            lb.insert(tk.END,"  "+short+("   ·   "+rest[:60] if rest else ""))
        try:
            self.window.update_idletasks()
            f=self.search_entry.master
            x=f.winfo_x()+self.search_entry.winfo_x()
            y=f.winfo_y()+self.search_entry.winfo_y()+self.search_entry.winfo_height()+2
            ew=self.search_entry.winfo_width()
        except Exception:
            x,y,ew=12,52,360
        lb.configure(height=min(7,len(rows)))
        self.suggest_frame.place(in_=self.shell,x=x,y=y,width=max(ew,220))
        self.suggest_frame.lift()

    def hide_suggestions(self):
        if getattr(self,"_suggest_after",None):
            try:self.window.after_cancel(self._suggest_after)
            except Exception:pass
            self._suggest_after=None
        if getattr(self,"suggest_frame",None):
            try:self.suggest_frame.place_forget()
            except Exception:pass

    def _focus_suggestions(self,e):
        if getattr(self,"suggest_frame",None) and self.suggest_frame.winfo_ismapped():
            self.suggest_list.focus_set()
            self.suggest_list.selection_clear(0,tk.END);self.suggest_list.selection_set(0);self.suggest_list.activate(0)
            return "break"

    def _suggest_click(self,e):
        try:idx=self.suggest_list.nearest(e.y)
        except Exception:idx=-1
        if idx>=0:self._use_suggestion(idx)
        return "break"

    def _use_suggestion(self,idx=None):
        rows=getattr(self,"suggest_rows",[])
        if idx is None:
            sel=self.suggest_list.curselection(); idx=sel[0] if sel else 0
        if 0<=idx<len(rows):
            name,lat,lon=rows[idx]
            self.query.set(name.split(",")[0].strip())
            self.hide_suggestions()
            self.go(lat,lon,name)
    def go(self,lat,lon,label):
        self.center_lat,self.center_lon,self.zoom=max(-MAX_LAT,min(MAX_LAT,float(lat))),self._normal_lon(lon),max(self.zoom,14)
        lat,lon=self.center_lat,self.center_lon
        full=str(label);short=full.split(",")[0][:30];self.marker=(lat,lon,short)
        note=self.note_at(lat,lon)
        self.info_data={"title":short,"address":full,"lat":lat,"lon":lon,"note":note.get("text","") if note else ""}
        self.save_state();self.schedule()

    def inspect_point(self,lat,lon):
        self.inspect_seq+=1;seq=self.inspect_seq
        self.action_points=[];self.route_points=[];self.measure_points=[]
        self.marker=(lat,lon,"选中位置")
        note=self.note_at(lat,lon)
        self.info_data={"title":"选中位置","address":"正在查询地址…","lat":lat,"lon":lon,"note":note.get("text","") if note else ""}
        self.notice="已选择地图点。"
        self.status.set(self.notice);self.save_state();self.schedule()
        def work():
            result=None
            try:
                qs=urllib.parse.urlencode({"format":"jsonv2","lat":f"{lat:.7f}","lon":f"{lon:.7f}","zoom":18,"addressdetails":1})
                req=urllib.request.Request("https://nominatim.openstreetmap.org/reverse?"+qs,headers={"User-Agent":UA})
                with urllib.request.urlopen(req,timeout=15) as r:data=json.loads(r.read().decode())
                address=data.get("address") or {}
                label=(data.get("name") or address.get("amenity") or address.get("road") or
                       address.get("suburb") or address.get("city") or address.get("town") or "选中位置")
                result=(str(label),str(data.get("display_name") or label))
            except Exception:pass
            try:self.window.after(0,lambda:self.inspect_done(seq,lat,lon,result))
            except Exception:pass
        threading.Thread(target=work,daemon=True,name="Passer-MapInspect").start()

    def inspect_done(self,seq,lat,lon,result):
        if self.closed or seq!=self.inspect_seq:return
        if result:
            label,address=result;self.marker=(lat,lon,label[:30])
            self.info_data.update({"title":label,"address":address})
        else:self.info_data.update({"address":"地址查询失败"})
        self.status.set(self.notice);self.save_state();self.schedule()

    def screen_to_geo(self,x,y):
        cx,cy=to_world(self.center_lat,self.center_lon,self.zoom)
        return to_geo(cx+x-self.canvas.winfo_width()/2,cy+y-self.canvas.winfo_height()/2,self.zoom)

    def show_map_menu(self,e):
        if self.mode=="measure":
            self.mode=None
            self.notice=(f"测距完成：{len(self.measure_points)} 点，总距离 {self.format_distance(self.measure_total())}"
                         if len(self.measure_points)>=2 else "已退出测距。")
            self.status.set(self.notice); self.schedule(); return
        self.context_point=self.screen_to_geo(e.x,e.y)
        try:self.map_menu.tk_popup(e.x_root,e.y_root)
        finally:self.map_menu.grab_release()

    def add_note(self):
        if not self.context_point:return
        text=simpledialog.askstring("添加备注","请输入该点备注：",parent=self.window)
        if not text or not text.strip():return
        self._push_undo()
        lat,lon=self.context_point;text=text.strip()
        existing=self.note_at(lat,lon,18)
        if existing:existing.update({"lat":lat,"lon":lon,"text":text})
        else:self.notes.append({"lat":lat,"lon":lon,"text":text})
        self.save_state();self.inspect_point(lat,lon)
        self.notice="备注已保存，正在查询地点信息…";self.status.set(self.notice)


    def open_location(self,lat,lon,zoom=15,title=""):
        self.center_lat=max(-MAX_LAT,min(MAX_LAT,float(lat)))
        self.center_lon=((float(lon)+180.0)%360.0)-180.0
        self.zoom=max(2,min(19,int(zoom)))
        label=str(title or "").strip() or f"{self.center_lat:.6f}, {self.center_lon:.6f}"
        self.marker=(self.center_lat,self.center_lon,label[:30])
        self.info_data={"lat":self.center_lat,"lon":self.center_lon,"title":label,"address":label}
        self.query.set(label)
        self.notice=f"已定位：{label}";self.status.set(self.notice)
        self.save_state();self.schedule()

    @staticmethod
    def parse_saved_location_target(target):
        try:
            parsed=urllib.parse.urlparse(str(target or ""))
            if parsed.scheme.casefold()!="passer-map" or parsed.netloc.casefold()!="location":return None
            values=urllib.parse.parse_qs(parsed.query)
            lat=float((values.get("lat") or [""])[0]);lon=float((values.get("lon") or [""])[0])
            zoom=max(2,min(19,int((values.get("zoom") or [15])[0])))
            if not MapWindow._valid_coord(lat,lon):return None
            return lat,MapWindow._normal_lon(lon),zoom
        except (TypeError,ValueError,IndexError):return None

    def _saved_location_items(self):
        rows=[]
        for item in getattr(self.app,"items",[]):
            if getattr(item,"kind","")!="map_location":continue
            parsed=self.parse_saved_location_target(getattr(item,"target",""))
            if parsed is not None:rows.append((item,parsed))
        return rows

    def _title_for_point(self,lat,lon):
        coords=f"{lat:.6f}, {lon:.6f}"
        note=self.note_at(lat,lon,18)
        if note and str(note.get("text") or "").strip():return str(note["text"]).strip()
        if self.info_data:
            try:same=abs(float(self.info_data.get("lat"))-lat)<0.00002 and abs(float(self.info_data.get("lon"))-lon)<0.00002
            except Exception:same=False
            if same:
                title=str(self.info_data.get("title") or self.info_data.get("address") or "").strip()
                if title:return title
        if self.marker:
            try:same=abs(float(self.marker[0])-lat)<0.00002 and abs(float(self.marker[1])-lon)<0.00002
            except Exception:same=False
            if same and str(self.marker[2] or "").strip():return str(self.marker[2]).strip()
        return coords

    def save_current_location(self):
        if self.marker and self._valid_coord(self.marker[0],self.marker[1]):lat,lon=float(self.marker[0]),self._normal_lon(self.marker[1])
        else:lat,lon=float(self.center_lat),self._normal_lon(self.center_lon)
        title=self._title_for_point(lat,lon)
        try:self.app.add_map_location(lat,lon,self.zoom,title)
        except Exception:
            self.notice="地点保存失败。";self.status.set(self.notice);return
        self.notice=f"已保存地点：{title}";self.status.set(self.notice)
        self.save_state()

    def _load_saved_location(self,item,parsed):
        lat,lon,zoom=parsed
        title=str(getattr(item,"display_title","") or getattr(item,"title","") or f"{lat:.6f}, {lon:.6f}")
        self.open_location(lat,lon,zoom,title)
        self.notice=f"已加载保存地点：{title}";self.status.set(self.notice)

    def show_saved_locations(self):
        rows=self._saved_location_items()
        menu=self.saved_locations_menu
        menu.delete(0,tk.END)
        if not rows:
            menu.add_command(label="暂无保存地点",state=tk.DISABLED)
        else:
            visible=rows[-40:]
            for item,parsed in reversed(visible):
                label=str(getattr(item,"display_title","") or getattr(item,"title","") or "地图地点")
                menu.add_command(label=label[:70],command=lambda i=item,p=parsed:self._load_saved_location(i,p))
            if len(rows)>len(visible):
                menu.add_separator();menu.add_command(label=f"另有 {len(rows)-len(visible)} 个地点，请在 Passer 面板中打开",state=tk.DISABLED)
        try:
            x=self.load_location_button.winfo_rootx();y=self.load_location_button.winfo_rooty()+self.load_location_button.winfo_height()
            menu.tk_popup(x,y)
        finally:
            try:menu.grab_release()
            except tk.TclError:pass

    def add_to_passer(self):
        if not self.context_point:return
        lat,lon=self.context_point
        coords=f"{lat:.6f}, {lon:.6f}"
        title=self._title_for_point(lat,lon)
        try:item_id=self.app.add_map_location(lat,lon,self.zoom,title)
        except Exception:return
        if title==coords:self._resolve_passer_place(lat,lon,item_id)

    def _resolve_passer_place(self,lat,lon,item_id):
        def work():
            name=None
            try:
                qs=urllib.parse.urlencode({"format":"jsonv2","lat":f"{lat:.7f}","lon":f"{lon:.7f}","zoom":18,"addressdetails":1})
                req=urllib.request.Request("https://nominatim.openstreetmap.org/reverse?"+qs,headers={"User-Agent":UA})
                with urllib.request.urlopen(req,timeout=15) as response:data=json.loads(response.read().decode())
                address=data.get("address") or {}
                name=(data.get("name") or address.get("amenity") or address.get("road") or
                      address.get("suburb") or address.get("city") or address.get("town"))
            except Exception:name=None
            if name:
                try:self.window.after(0,lambda:self.app.update_map_location_title(item_id,str(name)))
                except Exception:pass
        threading.Thread(target=work,daemon=True,name="Passer-MapSavePlace").start()

    def add_plan_here(self):
        """右键「添加计划」：把当前点位作为目标地点带入计划工具。"""
        if not self.context_point:return
        lat,lon=self.context_point
        note=self.note_at(lat,lon,18)
        coords=f"{lat:.6f}, {lon:.6f}"
        place=(str(note.get("text","")).strip() if note else "") or coords
        try:self.app.add_to_plan(event="地图地点提醒",place=place)
        except Exception:pass
        if place==coords:
            self._resolve_plan_place(lat,lon,coords)   # 后台反查地名替换坐标

    def _resolve_plan_place(self,lat,lon,coords):
        def work():
            name=None
            try:
                qs=urllib.parse.urlencode({"format":"jsonv2","lat":f"{lat:.7f}","lon":f"{lon:.7f}","zoom":18,"addressdetails":1})
                req=urllib.request.Request("https://nominatim.openstreetmap.org/reverse?"+qs,headers={"User-Agent":UA})
                with urllib.request.urlopen(req,timeout=15) as r:data=json.loads(r.read().decode())
                addr=data.get("address") or {}
                name=(data.get("name") or addr.get("amenity") or addr.get("road") or
                      addr.get("suburb") or addr.get("city") or addr.get("town"))
            except Exception:name=None
            if name:
                try:self.window.after(0,lambda n=str(name):self._apply_plan_place(coords,n))
                except Exception:pass
        threading.Thread(target=work,daemon=True,name="Passer-PlanPlace").start()

    def _apply_plan_place(self,old_coords,name):
        pw=getattr(self.app,"plan_window",None)
        if pw is None or getattr(pw,"closed",True):return
        try:
            if pw.place_var.get().strip()==old_coords:
                pw.place_var.set(name)
                pw._refresh_attach_buttons()
        except Exception:pass

    def note_at(self,lat,lon,pixels=16):
        px,py=to_world(lat,lon,self.zoom)
        for note in reversed(self.notes):
            nx,ny=to_world(float(note["lat"]),float(note["lon"]),self.zoom)
            if math.hypot(nx-px,ny-py)<=pixels:return note
        return None

    def draw_info_bubble(self):
        if not self.info_data:
            self.info_bubble.place_forget();return
        info=self.info_data;parts=[str(info.get("title") or "地图点位")]
        address=str(info.get("address") or "").strip()
        if address and address!=parts[0]:parts.append(address)
        parts.append(f"坐标：{float(info['lat']):.6f}, {float(info['lon']):.6f}")
        note=str(info.get("note") or "").strip()
        if note:parts.append(f"备注：{note}")
        content="\n".join(parts)
        self.info_bubble.configure(state=tk.NORMAL)
        self.info_bubble.delete("1.0",tk.END);self.info_bubble.insert("1.0",content)
        self.info_bubble.configure(state=tk.DISABLED)
        width=470
        # 先按目标宽度放置，让文本按像素宽度真实换行，再据“实际显示行数”定高，
        # 避免按字符数估算导致中文长地址被裁掉最后一行。
        self.info_bubble.place(x=14,rely=1.0,y=-14,anchor=tk.SW,width=width,height=1)
        self.info_bubble.update_idletasks()
        try:
            measured=self.info_bubble.count("1.0","end-1c","displaylines")
            lines=measured[0] if isinstance(measured,(tuple,list)) else int(measured)
        except Exception:
            lines=0
        lines=max(lines or 0,len(parts),1)
        if getattr(self,"_info_line_h",0)<=0:
            try:
                self._info_line_h=tkfont.Font(self.info_bubble,font=self.info_bubble.cget("font")).metrics("linespace")
            except Exception:
                self._info_line_h=20
        # 行高*行数 + 上下内边距(pady=9 各一份) + 边框(bd=1 各一份) + 余量。
        height=lines*self._info_line_h+2*9+2*1+6
        height=min(height,max(60,self.canvas.winfo_height()-28))
        self.info_bubble.place(x=14,rely=1.0,y=-14,anchor=tk.SW,width=width,height=height)
        self.info_bubble.lift()

    def select_all_info(self,_event=None):
        self.info_bubble.tag_add(tk.SEL,"1.0","end-1c")
        self.info_bubble.mark_set(tk.INSERT,"1.0")
        self.info_bubble.see(tk.INSERT)
        return "break"

    def clear_actions(self):
        self.mode=None; self.action_points=[]; self.route_points=[]; self.measure_points=[]; self.notice=""

    def set_mode(self,mode):
        self.hide_suggestions()
        self._push_undo()
        self.mode=mode; self.action_points=[]; self.route_points=[]; self.measure_points=[]
        self.info_data=None
        self.notice="请在地图上点击起点。" if mode=="route" else "请在地图上点击测距第一点，可连续多点，右键结束测距。"
        self.status.set(self.notice); self.schedule()

    @staticmethod
    def distance(a,b):
        lat1,lon1,lat2,lon2=map(math.radians,(a[0],a[1],b[0],b[1])); dlat=lat2-lat1;dlon=lon2-lon1
        h=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        return 12742000*math.asin(math.sqrt(h))

    @staticmethod
    def format_distance(m): return f"{m/1000:.2f} 公里" if m>=1000 else f"{m:.0f} 米"

    def measure_total(self):
        return sum(self.distance(self.measure_points[i-1],self.measure_points[i]) for i in range(1,len(self.measure_points)))

    def measure_summary(self):
        n=len(self.measure_points)
        if n==0: return "请在地图上点击测距第一点。"
        if n==1: return "已选第 1 点，继续点击添加测距点，右键结束测距。"
        return f"测距：{n} 点，总距离 {self.format_distance(self.measure_total())}（右键结束）"

    def locate(self):
        self._push_undo()
        self.notice="正在获取当前位置…"; self.status.set(self.notice)
        def work():
            result=None
            script=("Add-Type -AssemblyName System.Device;"
                    "$w=New-Object System.Device.Location.GeoCoordinateWatcher;"
                    "$ok=$w.TryStart($false,[TimeSpan]::FromSeconds(8));"
                    "if($ok -and -not $w.Position.Location.IsUnknown){"
                    "$c=$w.Position.Location; Write-Output ($c.Latitude.ToString([Globalization.CultureInfo]::InvariantCulture)+','+$c.Longitude.ToString([Globalization.CultureInfo]::InvariantCulture))};$w.Stop()")
            try:
                flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
                p=subprocess.run(["powershell","-NoProfile","-WindowStyle","Hidden","-Command",script],capture_output=True,text=True,timeout=12,creationflags=flags)
                m=re.search(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",p.stdout)
                if m:result=(float(m.group(1)),float(m.group(2)),"我的位置")
            except Exception:pass
            if result is None:
                try:
                    req=urllib.request.Request("https://ipwho.is/",headers={"User-Agent":UA})
                    with urllib.request.urlopen(req,timeout=10) as r:data=json.loads(r.read().decode())
                    if data.get("success",True) and data.get("latitude") is not None:
                        result=(float(data["latitude"]),float(data["longitude"]),"网络定位")
                except Exception:pass
            try:self.window.after(0,lambda:self.locate_done(result))
            except Exception:pass
        threading.Thread(target=work,daemon=True,name="Passer-MapLocate").start()

    def locate_done(self,result):
        if self.closed:return
        if not result:self.notice="定位失败，请检查 Windows 定位权限或网络。";self.status.set(self.notice);return
        self.notice="定位成功。";self.go(*result);self.status.set(self.notice)

    def request_route(self):
        a,b=self.action_points[:2]; self.notice="正在规划驾车路线…";self.status.set(self.notice)
        def work():
            result=None
            try:
                url=f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=full&geometries=geojson&steps=false"
                req=urllib.request.Request(url,headers={"User-Agent":UA})
                with urllib.request.urlopen(req,timeout=20) as r:data=json.loads(r.read().decode())
                if data.get("code")=="Ok" and data.get("routes"):
                    route=data["routes"][0];points=[(p[1],p[0]) for p in route["geometry"]["coordinates"]]
                    result=(points,float(route["distance"]),float(route["duration"]))
            except Exception:pass
            try:self.window.after(0,lambda:self.route_done(result))
            except Exception:pass
        threading.Thread(target=work,daemon=True,name="Passer-MapRoute").start()

    def route_done(self,result):
        if self.closed:return
        if not result:self.notice="路线规划失败，请检查网络或重新选择起终点。";self.status.set(self.notice);return
        self.route_points,duration_distance,duration=result
        self.notice=f"驾车路线：{self.format_distance(duration_distance)}，约 {max(1,round(duration/60))} 分钟"
        self.fit_points(self.route_points);self.status.set(self.notice);self.save_state();self.schedule()

    def fit_points(self,points):
        if not points:return
        minlat=min(p[0] for p in points);maxlat=max(p[0] for p in points);minlon=min(p[1] for p in points);maxlon=max(p[1] for p in points)
        self.center_lat=(minlat+maxlat)/2;self.center_lon=(minlon+maxlon)/2
        w=max(200,self.canvas.winfo_width()-100);h=max(200,self.canvas.winfo_height()-100)
        for z in range(18,1,-1):
            x1,y1=to_world(minlat,minlon,z);x2,y2=to_world(maxlat,maxlon,z)
            if abs(x2-x1)<=w and abs(y2-y1)<=h:self.zoom=z;break

    def _push_undo(self):
        state=(self.center_lat,self.center_lon,self.zoom,copy.deepcopy(self.marker),
               copy.deepcopy(self.info_data),copy.deepcopy(self.notes),copy.deepcopy(self.action_points),
               copy.deepcopy(self.route_points),self.mode,self.notice,copy.deepcopy(self.measure_points))
        if not self.undo_stack or self.undo_stack[-1]!=state:
            self.undo_stack.append(state)
            if len(self.undo_stack)>40:self.undo_stack.pop(0)

    def undo(self):
        if not self.undo_stack:
            self.status.set("没有可撤销的地图操作。")
            return
        (self.center_lat,self.center_lon,self.zoom,self.marker,self.info_data,self.notes,
         self.action_points,self.route_points,self.mode,self.notice,self.measure_points)=self.undo_stack.pop()
        self.inspect_seq+=1
        self.save_state();self.status.set("已撤销上一步地图操作。");self.schedule()
    def show(self):
        if not self.closed:self.window.deiconify();self.window.lift();self.window.focus_force()
    def close(self):
        if not self.closed:
            self.save_state()
            self.closed=True
            self.hide_suggestions()
            if self.tile_poll_id is not None:
                try:self.window.after_cancel(self.tile_poll_id)
                except Exception:pass
            try:self.tile_pool.shutdown(wait=False,cancel_futures=True)
            except Exception:pass
            try:self.window.destroy()
            except Exception:pass
