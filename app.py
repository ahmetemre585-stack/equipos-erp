import flet as ft
import psycopg2
import psycopg2.extras
import urllib.parse
import time
import threading
import os
import csv
import hashlib
from datetime import datetime, timedelta
from contextlib import contextmanager

# NEON POSTGRESQL BULUT VERİTABANI BAĞLANTISI
DB_URL = "postgresql://neondb_owner:npg_D3S2NBfvnuIJ@ep-falling-glitter-b5fs6oio-pooler.c-7.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
APP_VERSION = "6.0 ERP (Bulut / PostgreSQL)"

# =============================================================
# YARDIMCI / VERİTABANI
# =============================================================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def money(v):
    return f"₺{float(v or 0):,.2f}"

def num(v, default=0.0):
    try:
        return float(str(v).replace(",", ".").strip())
    except Exception:
        return default

def hash_pin(pin: str) -> str:
    return hashlib.sha256(str(pin).encode("utf-8")).hexdigest()

def get_db():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    return conn

@contextmanager
def db_cursor(commit=False):
    conn = get_db()
    try:
        # SQLite'daki Row Factory yerine PostgreSQL RealDictCursor kullanıyoruz
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def column_exists(c, table, column):
    c.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name=%s", (table, column))
    return c.fetchone() is not None

def add_column(c, table, column, definition):
    if not column_exists(c, table, column):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db():
    with db_cursor(commit=True) as c:
        # SQLite AUTOINCREMENT yerine PostgreSQL SERIAL kullanıldı
        c.execute("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, pin TEXT NOT NULL, role TEXT DEFAULT 'yonetici', is_active INTEGER DEFAULT 1, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS tables (id SERIAL PRIMARY KEY, name TEXT NOT NULL, section TEXT DEFAULT 'Bahçe', status TEXT DEFAULT 'empty', total_amount REAL DEFAULT 0, start_time TEXT, staff_name TEXT, capacity INTEGER DEFAULT 4, pos_x REAL DEFAULT 0, pos_y REAL DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id SERIAL PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price REAL NOT NULL, is_active INTEGER DEFAULT 1, barcode TEXT, description TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS order_items (id SERIAL PRIMARY KEY, table_id INTEGER, product_id INTEGER, product_name TEXT, quantity INTEGER, unit_price REAL, note TEXT DEFAULT '', sent_to_kitchen INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS cash_transactions (id SERIAL PRIMARY KEY, transaction_type TEXT NOT NULL, amount REAL NOT NULL, category TEXT NOT NULL, description TEXT, staff_name TEXT, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS customer_accounts (id SERIAL PRIMARY KEY, full_name TEXT NOT NULL, phone TEXT, account_type TEXT DEFAULT 'Müdavim', current_balance REAL DEFAULT 0, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS couriers (id SERIAL PRIMARY KEY, name TEXT NOT NULL, is_active INTEGER DEFAULT 1)")
        c.execute("CREATE TABLE IF NOT EXISTS raw_materials (id SERIAL PRIMARY KEY, name TEXT NOT NULL, unit TEXT NOT NULL, unit_cost REAL NOT NULL, current_stock REAL DEFAULT 0, critical_level REAL DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS recipes (id SERIAL PRIMARY KEY, product_id INTEGER, raw_material_id INTEGER, quantity_needed REAL NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS sales (id SERIAL PRIMARY KEY, table_name TEXT, order_source TEXT DEFAULT 'Masa', total_amount REAL, total_cost REAL DEFAULT 0, profit_amount REAL DEFAULT 0, payment_type TEXT, cash_amount REAL DEFAULT 0, card_amount REAL DEFAULT 0, items_summary TEXT, staff_name TEXT, customer_name TEXT, courier_id INTEGER, customer_id INTEGER, status TEXT DEFAULT 'completed', created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS sale_items (id SERIAL PRIMARY KEY, sale_id INTEGER, product_id INTEGER, product_name TEXT, quantity INTEGER, unit_price REAL, total_price REAL, unit_cost REAL DEFAULT 0)")

        # ERP Tabloları
        c.execute("CREATE TABLE IF NOT EXISTS stock_movements (id SERIAL PRIMARY KEY, raw_material_id INTEGER, movement_type TEXT, quantity REAL, before_stock REAL, after_stock REAL, unit_cost REAL, reference TEXT, staff_name TEXT, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS waste_records (id SERIAL PRIMARY KEY, raw_material_id INTEGER, quantity REAL, reason TEXT, unit_cost REAL, total_cost REAL, staff_name TEXT, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS suppliers (id SERIAL PRIMARY KEY, name TEXT NOT NULL, phone TEXT, note TEXT, is_active INTEGER DEFAULT 1)")
        c.execute("CREATE TABLE IF NOT EXISTS purchases (id SERIAL PRIMARY KEY, supplier_id INTEGER, invoice_no TEXT, total_amount REAL DEFAULT 0, note TEXT, staff_name TEXT, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS purchase_items (id SERIAL PRIMARY KEY, purchase_id INTEGER, raw_material_id INTEGER, quantity REAL, unit_cost REAL, total_cost REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS shifts (id SERIAL PRIMARY KEY, staff_name TEXT, opened_at TEXT, closed_at TEXT, opening_cash REAL DEFAULT 0, closing_cash REAL, expected_cash REAL, status TEXT DEFAULT 'open', note TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS discounts (id SERIAL PRIMARY KEY, name TEXT, kind TEXT DEFAULT 'percent', value REAL DEFAULT 0, min_total REAL DEFAULT 0, active INTEGER DEFAULT 1, starts_at TEXT, ends_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS reservations (id SERIAL PRIMARY KEY, customer_name TEXT, phone TEXT, table_id INTEGER, reservation_time TEXT, party_size INTEGER DEFAULT 2, status TEXT DEFAULT 'Bekliyor', note TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS loyalty (id SERIAL PRIMARY KEY, customer_id INTEGER UNIQUE, points INTEGER DEFAULT 0, total_spend REAL DEFAULT 0, visit_count INTEGER DEFAULT 0, last_visit TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS campaigns (id SERIAL PRIMARY KEY, name TEXT, description TEXT, discount_percent REAL DEFAULT 0, min_total REAL DEFAULT 0, active INTEGER DEFAULT 1, starts_at TEXT, ends_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS audit_log (id SERIAL PRIMARY KEY, staff_name TEXT, action TEXT, detail TEXT, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS daily_targets (id SERIAL PRIMARY KEY, target_date TEXT UNIQUE, revenue_target REAL DEFAULT 0, profit_target REAL DEFAULT 0)")

        # Geriye uyumluluk kontrolleri
        for tbl, col, definition in [
            ("users", "role", "TEXT DEFAULT 'yonetici'"), ("users", "is_active", "INTEGER DEFAULT 1"), ("users", "created_at", "TEXT"),
            ("tables", "capacity", "INTEGER DEFAULT 4"), ("tables", "pos_x", "REAL DEFAULT 0"), ("tables", "pos_y", "REAL DEFAULT 0"),
            ("products", "barcode", "TEXT"), ("products", "description", "TEXT"),
            ("order_items", "note", "TEXT DEFAULT ''"), ("order_items", "sent_to_kitchen", "INTEGER DEFAULT 0"),
            ("sales", "customer_name", "TEXT"), ("sales", "courier_id", "INTEGER"), ("sales", "status", "TEXT DEFAULT 'completed'"),
            ("sales", "commission_amount", "REAL DEFAULT 0")
        ]:
            add_column(c, tbl, col, definition)

        defaults = {
            "timer_enabled":"1", "sound_enabled":"1", 
            "pos_comm":"2.5", "trendyol_comm":"38", "ys_comm":"35", "getir_comm":"38"
        }
        for k, v in defaults.items():
            # PostgreSQL ON CONFLICT yapısı
            c.execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT (key) DO NOTHING", (k, v))

        c.execute("SELECT COUNT(*) AS n FROM users")
        if c.fetchone()["n"] == 0:
            c.execute("INSERT INTO users(username,pin,role,is_active,created_at) VALUES(%s,%s,%s,%s,%s)", ("yonetici", hash_pin("9999"), "yonetici", 1, now_str()))
            for i in range(1,5): c.execute("INSERT INTO tables(name,section,capacity) VALUES(%s,%s,%s)", (f"Bahçe {i}", "Bahçe", 4))
            for i in range(1,4): c.execute("INSERT INTO tables(name,section,capacity) VALUES(%s,%s,%s)", (f"Salon {i}", "İçerisi", 4))

def get_setting(key, default=""):
    with db_cursor() as c:
        c.execute("SELECT value FROM settings WHERE key=%s", (key,))
        r = c.fetchone()
        return r["value"] if r else default

def set_setting(key, value):
    with db_cursor(commit=True) as c:
        c.execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, str(value)))

def audit(staff, action, detail=""):
    try:
        with db_cursor(commit=True) as c:
            c.execute("INSERT INTO audit_log(staff_name,action,detail,created_at) VALUES(%s,%s,%s,%s)", (staff or "sistem", action, detail, now_str()))
    except Exception: pass

def play_sound(page, kind="click"):
    if not page: return
    if get_setting("sound_enabled", "1") != "1": return
    freq = 650 if kind == "click" else (280 if kind == "delete" else 950)
    js = f"""try{{let c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();o.frequency.value={freq};g.gain.value=.08;o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+.09)}}catch(e){{}}"""
    try: page.run_js(js)
    except Exception: pass

def close_dlg(page, dlg):
    if page: page.close(dlg)

def snackbar(page, text, color=None):
    if page: page.open(ft.SnackBar(content=ft.Text(text), bgcolor=color))

def calculate_recipe_cost(c, product_id, qty=1):
    c.execute("SELECT r.quantity_needed, r.raw_material_id, m.unit_cost FROM recipes r JOIN raw_materials m ON m.id=r.raw_material_id WHERE r.product_id=%s", (product_id,))
    return sum((r["quantity_needed"] * qty * r["unit_cost"]) for r in c.fetchall())

def check_stock(c, product_id, qty):
    c.execute("SELECT r.quantity_needed, r.raw_material_id, m.current_stock, m.name, m.unit FROM recipes r JOIN raw_materials m ON m.id=r.raw_material_id WHERE r.product_id=%s", (product_id,))
    for r in c.fetchall():
        if r["current_stock"] < r["quantity_needed"] * qty - 1e-9:
            return False, f"{r['name']} stok yetersiz ({r['current_stock']} {r['unit']})"
    return True, ""

def deduct_stock(c, product_id, qty, staff, reference):
    c.execute("SELECT r.quantity_needed, r.raw_material_id, m.current_stock, m.unit_cost FROM recipes r JOIN raw_materials m ON m.id=r.raw_material_id WHERE r.product_id=%s", (product_id,))
    rows = c.fetchall()
    total_cost = 0
    for r in rows:
        needed = r["quantity_needed"] * qty
        before = r["current_stock"]
        after = before - needed
        c.execute("UPDATE raw_materials SET current_stock=%s WHERE id=%s", (after, r["raw_material_id"]))
        c.execute("INSERT INTO stock_movements(raw_material_id,movement_type,quantity,before_stock,after_stock,unit_cost,reference,staff_name,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", (r["raw_material_id"], "SATIŞ", -needed, before, after, r["unit_cost"], reference, staff, now_str()))
        total_cost += needed * r["unit_cost"]
    return total_cost

def calc_commission(source, total_amount, card_amount):
    comm = 0.0
    if source == "Trendyol": comm += total_amount * (num(get_setting("trendyol_comm", "38")) / 100.0)
    elif source == "Yemeksepeti": comm += total_amount * (num(get_setting("ys_comm", "35")) / 100.0)
    elif source == "Getir": comm += total_amount * (num(get_setting("getir_comm", "38")) / 100.0)
    
    if card_amount > 0:
        comm += card_amount * (num(get_setting("pos_comm", "2.5")) / 100.0)
    return comm

# =============================================================
# UI BASE 
# =============================================================

class BaseView(ft.View):
    def __init__(self, page, route):
        super().__init__(route=route, padding=12)
        self.username = page.session.get("username") or "sistem"
        
    @property
    def user(self): return self.username
    def allowed(self, permission): return True
    
    def update_page(self, e=None):
        pg = self._pg(e)
        if pg:
            pg.update()
        else:
            try:
                self.update()
            except Exception:
                pass
            
    def go(self, route): 
        if hasattr(self, "page") and self.page: self.page.go(route)
        
    def back(self): 
        if hasattr(self, "page") and self.page: self.page.go("/dashboard")
        
    def toggle_dark(self, e):
        if hasattr(self, "page") and self.page:
            self.page.theme_mode = ft.ThemeMode.DARK if self.page.theme_mode == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT
            self.page.update()
            
    def _pg(self, e=None):
        p = getattr(e, "page", None) if e else None
        if p: return p
        return self.page if hasattr(self, "page") else None

    def confirm(self, title, message, callback, danger=False, e=None):
        pg = self._pg(e)
        def cb(ev):
            p = self._pg(ev) or pg
            if p: p.close(dlg)
            callback()
        dlg = ft.AlertDialog(modal=True, title=ft.Text(title), content=ft.Text(message), actions=[ft.TextButton("İptal", on_click=lambda ev: (self._pg(ev) or pg).close(dlg) if (self._pg(ev) or pg) else None), ft.ElevatedButton("Onayla", bgcolor=ft.colors.RED_600 if danger else "#EC4899", color=ft.colors.WHITE, on_click=cb)])
        if pg: pg.open(dlg)
        else: print("UYARI: confirm() için geçerli bir page bulunamadı, dialog açılamadı.")

def footer():
    return ft.Container(content=ft.Text(f"⚡ Powered by EMREPASS Company • {APP_VERSION}", size=10, color=ft.colors.GREY_500, text_align=ft.TextAlign.CENTER), padding=8, alignment=ft.alignment.center)

def header(page, title, back=True):
    return ft.Row([
        ft.IconButton(ft.icons.ARROW_BACK, on_click=lambda e: e.page.go("/dashboard")) if back else ft.Container(width=40), 
        ft.Text(title, size=19, weight=ft.FontWeight.BOLD), 
        ft.Row([
            ft.IconButton(ft.icons.BRIGHTNESS_4_OUTLINED, on_click=lambda e: toggle_page_dark(e.page)), 
            ft.Text(page.session.get("username") or "", size=11, color="#10B981")
        ])
    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

def toggle_page_dark(page):
    if page:
        page.theme_mode = ft.ThemeMode.DARK if page.theme_mode == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT
        page.update()

# =============================================================
# LOGIN / DASHBOARD
# =============================================================

class SplashView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/splash")
        self.controls=[ft.Container(expand=True,alignment=ft.alignment.center,content=ft.Column([ft.Image(src="logo.png",width=180,height=140),ft.Text("Equipos",size=26,weight=ft.FontWeight.BOLD,color="#DB2777"),ft.Text("Yönetim sistemi hazırlanıyor...",color=ft.colors.GREY_600),ft.ProgressRing()],horizontal_alignment=ft.CrossAxisAlignment.CENTER))]
        threading.Thread(target=lambda:(time.sleep(.8),page.go("/login")),daemon=True).start()

class LoginView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/login")
        self.u=ft.TextField(label="Kullanıcı Adı",prefix_icon=ft.icons.PERSON)
        self.p=ft.TextField(label="PIN",password=True,max_length=4,prefix_icon=ft.icons.LOCK,keyboard_type=ft.KeyboardType.NUMBER,on_submit=self.login)
        self.err=ft.Text("",color=ft.colors.RED_600)
        self.controls=[ft.Container(expand=True,alignment=ft.alignment.center,content=ft.Column([ft.Container(width=380,padding=25,border_radius=18,border=ft.border.all(1,ft.colors.OUTLINE_VARIANT),content=ft.Column([ft.Image(src="logo.png",width=120,height=80),ft.Text("Equipos",size=25,weight=ft.FontWeight.BOLD,color="#DB2777"),ft.Text("Personel Girişi",color=ft.colors.GREY_600),self.u,self.p,self.err,ft.ElevatedButton("Giriş Yap",width=240,height=46,bgcolor="#EC4899",color=ft.colors.WHITE,on_click=self.login)],horizontal_alignment=ft.CrossAxisAlignment.CENTER,spacing=12)),ft.Text("Varsayılan yönetici: yonetici / 9999",size=10,color=ft.colors.GREY_500)],horizontal_alignment=ft.CrossAxisAlignment.CENTER))]
    def login(self,e):
        with db_cursor() as c:
            c.execute("SELECT username FROM users WHERE LOWER(username)=%s AND pin=%s AND is_active=1", ((self.u.value or "").strip().lower(), hash_pin(self.p.value or "")))
            r=c.fetchone()
        if not r:
            self.err.value="Hatalı kullanıcı adı, PIN veya pasif hesap."
            self.p.value=""; play_sound(self.page,"delete"); self.update_page(); return
        self.page.session.set("username",r["username"]); audit(r["username"],"GİRİŞ")
        play_sound(self.page,"success"); self.page.go("/dashboard")

class DashboardView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/dashboard")
        with db_cursor() as c:
            c.execute("SELECT COALESCE(SUM(total_amount),0) rev, COALESCE(SUM(profit_amount),0) prof, COUNT(*) cnt FROM sales WHERE SUBSTRING(created_at,1,10)=%s AND status='completed'",(today_str(),))
            st=c.fetchone()
            c.execute("SELECT COUNT(*) n FROM tables WHERE status='occupied'")
            occ=c.fetchone()["n"]
            c.execute("SELECT COUNT(*) n FROM raw_materials WHERE current_stock<=critical_level")
            low=c.fetchone()["n"]
        self.controls=[header(page,"Equipos",False),ft.Container(padding=12,border_radius=14,bgcolor=ft.colors.with_opacity(.08,"#EC4899"),content=ft.Row([ft.Text(f"💰 Ciro {money(st['rev'])}",weight=ft.FontWeight.BOLD,color="#059669"),ft.Text(f"📈 Kâr {money(st['prof'])}",weight=ft.FontWeight.BOLD,color="#8B5CF6"),ft.Text(f"🪑 Dolu {occ}"),ft.Text(f"⚠️ Kritik stok {low}",color=ft.colors.RED_600)],alignment=ft.MainAxisAlignment.SPACE_BETWEEN,wrap=True)),self.menu_grid(),footer()]
    def menu_grid(self):
        items=[("Masalar","Masa yönetimi",ft.icons.TABLE_RESTAURANT,"#EC4899","/tables","tables"),("Gel-Al & Paket","Hızlı satış & Kurye",ft.icons.SHOPPING_BAG,"#F59E0B","/quick-pos","pos"),("Kasa & Cari","Kasa, vardiya, cari",ft.icons.ACCOUNT_BALANCE_WALLET,"#3B82F6","/cash","cash"),("Stok & Reçete","Stok, maliyet, fire",ft.icons.INVENTORY,"#EAB308","/inventory","inventory"),("Ürünler","Menü ve fiyatlar",ft.icons.FASTFOOD,"#D97706","/products","products"),("Raporlar","Satış ve kârlılık",ft.icons.ANALYTICS,"#10B981","/reports","reports"),("Tedarikçiler","Satın alma",ft.icons.LOCAL_SHIPPING,"#6366F1","/suppliers","suppliers"),("Rezervasyon","Masa planı",ft.icons.EVENT_SEAT,"#F43F5E","/reservations","reservations"),("Müşteri & Sadakat","Puan ve cari",ft.icons.PEOPLE,"#14B8A6","/loyalty","loyalty"),("Kampanyalar","İndirim kuralları",ft.icons.LOCAL_OFFER,"#A855F7","/campaigns","campaigns"),("Vardiya & Z","Kasa kapanışı",ft.icons.LOCK_CLOCK,"#0EA5E9","/shifts","shifts"),("AI ERP","İşletme analizi",ft.icons.AUTO_AWESOME,"#8B5CF6","/ai-assistant","ai"),("Ayarlar","Sistem & Ekip",ft.icons.SETTINGS,ft.colors.GREY_700,"/settings","settings")]
        cards=[]
        for t,s,i,col,r,p in items:
            if self.allowed(p): cards.append(ft.Card(content=ft.Container(on_click=lambda e,rr=r:e.page.go(rr),padding=14,border_radius=14,content=ft.Column([ft.Icon(i,size=30,color=col),ft.Text(t,weight=ft.FontWeight.BOLD),ft.Text(s,size=10,color=ft.colors.GREY_600)],horizontal_alignment=ft.CrossAxisAlignment.CENTER,alignment=ft.MainAxisAlignment.CENTER))))
        return ft.GridView(expand=True,runs_count=2,max_extent=190,spacing=10,run_spacing=10,controls=cards)

# =============================================================
# STOK / FIRE / REÇETE
# =============================================================

class InventoryView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/inventory")
        self.rm=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True); self.rec=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True); self.mov=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True)
        self.tabs=ft.Tabs(expand=True,tabs=[ft.Tab(text="📦 Stok",content=ft.Column([ft.ElevatedButton("+ Hammadde",on_click=self.add_rm),self.rm],expand=True)),ft.Tab(text="📋 Reçete",content=ft.Column([ft.ElevatedButton("+ Reçete",on_click=self.add_recipe),self.rec],expand=True)),ft.Tab(text="🔥 Fire / Zayi",content=ft.Column([ft.ElevatedButton("+ Fire Kaydı",on_click=self.add_waste),self.mov],expand=True))])
        self.controls=[header(page,"Stok & Reçete"),self.tabs]; self.refresh()
    def refresh(self): self.load_rm(); self.load_recipes(); self.load_movements()
    def load_rm(self):
        self.rm.controls=[]
        with db_cursor() as c:
            c.execute("SELECT * FROM raw_materials ORDER BY name")
            for r in c.fetchall():
                critical=r["current_stock"]<=r["critical_level"]
                self.rm.controls.append(ft.Container(padding=10,border_radius=10,border=ft.border.all(1,ft.colors.RED_500 if critical else ft.colors.OUTLINE_VARIANT),content=ft.Row([ft.Column([ft.Text(r["name"],weight=ft.FontWeight.BOLD),ft.Text(f"{money(r['unit_cost'])} / {r['unit']}",size=11)],expand=True),ft.Column([ft.Text(f"{r['current_stock']:,.2f} {r['unit']}",color=ft.colors.RED_600 if critical else "#059669",weight=ft.FontWeight.BOLD),ft.Text(f"Kritik: {r['critical_level']}",size=10)],horizontal_alignment=ft.CrossAxisAlignment.END),ft.Row([ft.IconButton(ft.icons.ADD_BOX,icon_color="#3B82F6",tooltip="Stok ekle",on_click=lambda e,rr=r:self.add_stock(rr)),ft.IconButton(ft.icons.DELETE_FOREVER,icon_color=ft.colors.RED_500,on_click=lambda e,rr=r:self.delete_rm(rr))])])))
        self.update_page()
    def add_rm(self,e):
        n=ft.TextField(label="Hammadde"); u=ft.Dropdown(label="Birim",value="Gram",options=[ft.dropdown.Option(x) for x in ["Gram","Kg","Adet","Litre","Kutu"]]); cost=ft.TextField(label="Birim maliyeti (TL)"); crit=ft.TextField(label="Kritik seviye")
        def save(e):
            if not n.value.strip(): return
            with db_cursor(commit=True) as c:c.execute("INSERT INTO raw_materials(name,unit,unit_cost,critical_level) VALUES(%s,%s,%s,%s)",(n.value.strip(),u.value,num(cost.value),num(crit.value)))
            close_dlg(self.page,d); self.refresh()
        d=ft.AlertDialog(title=ft.Text("Hammadde Ekle"),content=ft.Column([n,u,cost,crit],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def add_stock(self,r):
        q=ft.TextField(label=f"Eklenecek {r['unit']}"); cost=ft.TextField(label="Yeni birim maliyet (boş = mevcut)",value=str(r["unit_cost"]))
        def save(e):
            qty=num(q.value); newcost=num(cost.value,r["unit_cost"])
            if qty<=0:return
            with db_cursor(commit=True) as c:
                before=r["current_stock"]; after=before+qty
                c.execute("UPDATE raw_materials SET current_stock=%s,unit_cost=%s WHERE id=%s",(after,newcost,r["id"]))
                c.execute("INSERT INTO stock_movements(raw_material_id,movement_type,quantity,before_stock,after_stock,unit_cost,reference,staff_name,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(r["id"],"GİRİŞ",qty,before,after,newcost,"Manuel stok girişi",self.user,now_str()))
            audit(self.user,"STOK GİRİŞİ",f"{r['name']} +{qty}");close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text(r["name"]),content=ft.Column([q,cost],tight=True),actions=[ft.ElevatedButton("Ekle",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def delete_rm(self,r):
        def f():
            with db_cursor(commit=True) as c:c.execute("DELETE FROM recipes WHERE raw_material_id=%s",(r["id"],));c.execute("DELETE FROM raw_materials WHERE id=%s",(r["id"],))
            self.refresh()
        self.confirm("Hammaddeyi Sil", f"'{r['name']}' silinecek. Emin misiniz? (Reçetelerden de temizlenir)", f, True)
    def add_recipe(self,e):
        with db_cursor() as c:
            c.execute("SELECT id,name FROM products WHERE is_active=1 ORDER BY name"); ps=c.fetchall(); c.execute("SELECT id,name,unit FROM raw_materials ORDER BY name"); rs=c.fetchall()
        p=ft.Dropdown(label="Ürün",options=[ft.dropdown.Option(str(x["id"]),x["name"]) for x in ps]); r=ft.Dropdown(label="Hammadde",options=[ft.dropdown.Option(str(x["id"]),f"{x['name']} ({x['unit']})") for x in rs]); q=ft.TextField(label="Ürün başına miktar")
        def save(e):
            if not p.value or not r.value:return
            with db_cursor(commit=True) as c:c.execute("INSERT INTO recipes(product_id,raw_material_id,quantity_needed) VALUES(%s,%s,%s)",(int(p.value),int(r.value),num(q.value)))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Reçeteye Hammadde Bağla"),content=ft.Column([p,r,q],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def load_recipes(self):
        self.rec.controls=[]
        with db_cursor() as c:
            c.execute("SELECT r.id,p.name product,m.name material,r.quantity_needed,m.unit FROM recipes r JOIN products p ON p.id=r.product_id JOIN raw_materials m ON m.id=r.raw_material_id ORDER BY p.name,m.name")
            for x in c.fetchall(): self.rec.controls.append(ft.ListTile(title=ft.Text(x["product"]),subtitle=ft.Text(f"{x['quantity_needed']} {x['unit']} {x['material']}"),trailing=ft.IconButton(ft.icons.DELETE,icon_color=ft.colors.RED_500,on_click=lambda e,i=x["id"]:self.del_recipe(i))))
    def del_recipe(self,i):
        with db_cursor(commit=True) as c:c.execute("DELETE FROM recipes WHERE id=%s",(i,));self.refresh()
    def add_waste(self,e):
        with db_cursor() as c:c.execute("SELECT id,name,unit,unit_cost,current_stock FROM raw_materials ORDER BY name"); rows=c.fetchall()
        rm=ft.Dropdown(label="Hammadde",options=[ft.dropdown.Option(str(x["id"]),f"{x['name']} ({x['unit']})") for x in rows]); q=ft.TextField(label="Miktar"); reason=ft.TextField(label="Neden (bozulma, dökülme vb.)")
        def save(e):
            qty=num(q.value); rr=next((x for x in rows if str(x["id"])==rm.value),None)
            if not rr or qty<=0 or qty>rr["current_stock"]:return
            with db_cursor(commit=True) as c:
                before=rr["current_stock"];after=before-qty;total=qty*rr["unit_cost"]
                c.execute("UPDATE raw_materials SET current_stock=%s WHERE id=%s",(after,rr["id"]))
                c.execute("INSERT INTO waste_records(raw_material_id,quantity,reason,unit_cost,total_cost,staff_name,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",(rr["id"],qty,reason.value,rr["unit_cost"],total,self.user,now_str()))
                c.execute("INSERT INTO stock_movements(raw_material_id,movement_type,quantity,before_stock,after_stock,unit_cost,reference,staff_name,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(rr["id"],"FİRE",-qty,before,after,rr["unit_cost"],reason.value,self.user,now_str()))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Fire / Zayi"),content=ft.Column([rm,q,reason],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def load_movements(self):
        self.mov.controls=[]
        with db_cursor() as c:c.execute("SELECT m.name,s.* FROM stock_movements s JOIN raw_materials m ON m.id=s.raw_material_id ORDER BY s.id DESC LIMIT 100");rows=c.fetchall()
        for x in rows:self.mov.controls.append(ft.ListTile(title=ft.Text(f"{x['name']} • {x['movement_type']}"),subtitle=ft.Text(f"{x['reference']} • {x['created_at']}"),trailing=ft.Text(f"{x['quantity']:+,.2f}")))

# =============================================================
# ÜRÜNLER
# =============================================================

class ProductsView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/products");self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Ürün Yönetimi"),ft.Row([ft.ElevatedButton("+ Ürün Ekle",on_click=self.add_product),ft.ElevatedButton("+ İndirim/Kampanya",on_click=lambda e:e.page.go("/campaigns"))]),self.list_col];self.refresh()
    def refresh(self):
        self.list_col.controls=[]
        with db_cursor() as c:c.execute("SELECT * FROM products ORDER BY category,name");rows=c.fetchall()
        for p in rows:
            self.list_col.controls.append(ft.Container(padding=10,border_radius=10,border=ft.border.all(1,ft.colors.OUTLINE_VARIANT),opacity=1 if p["is_active"] else .5,content=ft.Row([ft.Column([ft.Text(p["name"],weight=ft.FontWeight.BOLD),ft.Text(f"{p['category']} • {money(p['price'])}"),ft.Text(p["description"] or "",size=10,color=ft.colors.GREY_600)],expand=True),ft.IconButton(ft.icons.EDIT,on_click=lambda e,rr=p:self.edit(rr)),ft.IconButton(ft.icons.VISIBILITY if p["is_active"] else ft.icons.VISIBILITY_OFF,on_click=lambda e,i=p["id"],s=0 if p["is_active"] else 1:self.toggle(i,s)),ft.IconButton(ft.icons.DELETE,icon_color=ft.colors.RED_500,on_click=lambda e,rr=p:self.delete(rr))])))
        self.update_page()
    def add_product(self,e):self.product_dialog()
    def edit(self,p):self.product_dialog(p)
    def product_dialog(self,p=None):
        n=ft.TextField(label="Ürün adı",value=p["name"] if p else "");cat=ft.Dropdown(label="Kategori",value=p["category"] if p else "Waffle",options=[ft.dropdown.Option(x) for x in ["Waffle","Soğuk İçecek","Sıcak İçecek","Tatlı","Yiyecek","Diğer"]]);price=ft.TextField(label="Fiyat",value=str(p["price"]) if p else "");barcode=ft.TextField(label="Barkod",value=p["barcode"] if p else "");desc=ft.TextField(label="Açıklama",value=p["description"] if p else "")
        def save(e):
            with db_cursor(commit=True) as c:
                if p:c.execute("UPDATE products SET name=%s,category=%s,price=%s,barcode=%s,description=%s WHERE id=%s",(n.value,cat.value,num(price.value),barcode.value,desc.value,p["id"]))
                else:c.execute("INSERT INTO products(name,category,price,barcode,description) VALUES(%s,%s,%s,%s,%s)",(n.value,cat.value,num(price.value),barcode.value,desc.value))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Ürün Düzenle" if p else "Ürün Ekle"),content=ft.Column([n,cat,price,barcode,desc],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def toggle(self,i,s):
        with db_cursor(commit=True) as c:c.execute("UPDATE products SET is_active=%s WHERE id=%s",(s,i));self.refresh()
    def delete(self,p):
        def f():
            with db_cursor(commit=True) as c:c.execute("DELETE FROM recipes WHERE product_id=%s",(p["id"],));c.execute("DELETE FROM products WHERE id=%s",(p["id"],))
            self.refresh()
        self.confirm("Ürünü Sil",f"'{p['name']}' silinecek. Emin misiniz? (Bağlı reçeteler de silinir)",f,True)

# =============================================================
# MASALAR / ADİSYON / HIZLI ÖDEME 
# =============================================================

class TablesView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/tables")
        self.edit_mode=False
        self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True)
        
        self.btn_toggle = ft.ElevatedButton("🛠️ Düzenle (Ekle / Sil)", bgcolor="#3B82F6", color=ft.colors.WHITE, on_click=self.toggle_edit)
        self.btn_add = ft.ElevatedButton("+ Yeni Masa", bgcolor="#10B981", color=ft.colors.WHITE, on_click=self.add_table, visible=False)
        
        self.controls=[
            header(page,"Masalar"),
            ft.Row([self.btn_toggle, self.btn_add], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Divider(height=10, color=ft.colors.TRANSPARENT),
            self.list_col
        ]
        self.refresh()
        
    def toggle_edit(self,e):
        pg = self._pg(e)
        if pg: play_sound(pg, "click")
        self.edit_mode = not self.edit_mode
        
        if self.edit_mode:
            self.btn_toggle.text = "✅ Bitti"
            self.btn_toggle.bgcolor = ft.colors.RED_500
            self.btn_add.visible = True
        else:
            self.btn_toggle.text = "🛠️ Düzenle (Ekle / Sil)"
            self.btn_toggle.bgcolor = "#3B82F6"
            self.btn_add.visible = False
            
        self.refresh(e)
        
    def refresh(self,e=None):
        self.list_col.controls=[]
        with db_cursor() as c:c.execute("SELECT * FROM tables ORDER BY section,id");rows=c.fetchall()
        sections={}
        for r in rows:sections.setdefault(r["section"],[]).append(r)
        for sec,rs in sections.items():
            self.list_col.controls.append(ft.Text(sec,size=16,weight=ft.FontWeight.BOLD,color="#DB2777"))
            self.list_col.controls.append(ft.Row([self.card(r) for r in rs],wrap=True,spacing=10))
        self.update_page(e)
        
    def card(self,r):
        occ=r["status"]=="occupied"
        c=ft.Column([ft.Icon(ft.icons.TABLE_RESTAURANT,size=30,color="#EC4899" if occ else "#10B981"),ft.Text(r["name"],weight=ft.FontWeight.BOLD),ft.Text(money(r["total_amount"])),ft.Text(r["staff_name"] or "Boş",size=10),ft.Text(r["start_time"] or "",size=9,color=ft.colors.GREY_600)],horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        
        if self.edit_mode:
            c.controls.append(ft.IconButton(ft.icons.DELETE,icon_color=ft.colors.RED_500,on_click=lambda e,i=r["id"],n=r["name"]:self.del_table(i,n,e)))
            
        click_handler = (lambda e,i=r["id"]:e.page.go(f"/order/{i}")) if not self.edit_mode else None
        
        return ft.Container(width=165,height=145 if self.edit_mode else 130,padding=10,border_radius=14,bgcolor=ft.colors.with_opacity(.10,"#EC4899" if occ else "#10B981"),border=ft.border.all(1,"#EC4899" if occ else "#10B981"),on_click=click_handler,content=c)
        
    def del_table(self,tid,tname,e=None):
        def f():
            with db_cursor(commit=True) as c:c.execute("DELETE FROM order_items WHERE table_id=%s",(tid,));c.execute("DELETE FROM tables WHERE id=%s",(tid,))
            self.refresh(e)
        self.confirm("Masayı Sil",f"{tname} silinsin mi?",f,True,e=e)
        
    def add_table(self,e):
        n=ft.TextField(label="Masa adı");s=ft.Dropdown(label="Bölüm",value="Bahçe",options=[ft.dropdown.Option("Bahçe"),ft.dropdown.Option("İçerisi"),ft.dropdown.Option("Teras")]);cap=ft.TextField(label="Kapasite",value="4")
        pg = self._pg(e)
        def save(ev):
            if not n.value.strip(): return
            with db_cursor(commit=True) as c:c.execute("INSERT INTO tables(name,section,capacity) VALUES(%s,%s,%s)",(n.value.strip(),s.value,int(num(cap.value,4))))
            p = self._pg(ev) or pg
            if p: p.close(d)
            self.refresh(e)
        d=ft.AlertDialog(title=ft.Text("Masa Ekle"),content=ft.Column([n,s,cap],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)])
        if pg: pg.open(d)
        else: print("UYARI: add_table() için geçerli bir page bulunamadı, dialog açılamadı.")

class OrderView(BaseView):
    def __init__(self,page,table_id):
        super().__init__(page,f"/order/{table_id}")
        self.table_id=table_id; self.cat="Tümü"; self.split_count = 1
        self.orders=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True)
        self.products=ft.ListView(expand=True)
        self.total=ft.Text("₺0,00",size=20,weight=ft.FontWeight.BOLD,color="#059669")
        self.split_info=ft.Text("", size=12, weight=ft.FontWeight.BOLD, color="#EC4899")
        self.load_table()
        
        self.controls=[
            ft.Row([ft.IconButton(ft.icons.ARROW_BACK,on_click=lambda e:e.page.go("/tables")),ft.Text(self.table_name,size=18,weight=ft.FontWeight.BOLD),ft.IconButton(ft.icons.SWAP_HORIZ,on_click=self.transfer)],alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Row([
                ft.Container(expand=1,border=ft.border.all(1,ft.colors.OUTLINE_VARIANT),border_radius=12,padding=10,content=ft.Column([
                    ft.Text("Adisyon",weight=ft.FontWeight.BOLD),
                    self.orders,
                    ft.Divider(),
                    ft.Row([ft.Text("Böl:", size=11, weight=ft.FontWeight.BOLD)] + [ft.TextButton(str(i), on_click=lambda _, val=i: self.set_split(val)) for i in range(1, 6)], spacing=0),
                    ft.Row([ft.Text("Toplam:"), self.total],alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Row([ft.Text(""), self.split_info],alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Text("Hızlı Ödeme Kapat:", size=11, weight=ft.FontWeight.BOLD),
                    ft.Row([
                        ft.ElevatedButton("💵 NAKİT",bgcolor="#10B981",color=ft.colors.WHITE,expand=True,on_click=lambda _:self.quick_pay("Nakit")),
                        ft.ElevatedButton("💳 KART",bgcolor="#3B82F6",color=ft.colors.WHITE,expand=True,on_click=lambda _:self.quick_pay("Kredi Kartı"))
                    ]),
                    ft.Row([ft.ElevatedButton("⚖️ PARÇALI / İNDİRİM",bgcolor="#8B5CF6",color=ft.colors.WHITE,expand=True,on_click=self.payment)])
                ],expand=True)),
                ft.Container(expand=1,padding=10,content=ft.Column([
                    ft.Row([ft.TextButton(x,on_click=lambda e,c=x:self.set_cat(c)) for x in ["Tümü","Waffle","Soğuk İçecek","Sıcak İçecek","Tatlı","Yiyecek"]],scroll=ft.ScrollMode.ADAPTIVE),
                    self.products
                ],expand=True))
            ],expand=True)
        ]
        self.refresh()
        
    def set_split(self, val):
        play_sound(self.page, "click")
        self.split_count = val
        self.render_orders()
        
    def load_table(self):
        with db_cursor() as c:c.execute("SELECT * FROM tables WHERE id=%s",(self.table_id,));r=c.fetchone()
        if not r: self.table_name="Masa";return
        self.table_name=r["name"]
    def set_cat(self,c):self.cat=c;self.render_products()
    def refresh(self):self.render_orders();self.render_products()
    
    def render_orders(self):
        self.orders.controls=[]
        with db_cursor() as c:
            c.execute("SELECT * FROM order_items WHERE table_id=%s ORDER BY id",(self.table_id,))
            rows=c.fetchall()
            total=sum(x["quantity"]*x["unit_price"] for x in rows)
            
        self.total.value=money(total)
        if self.split_count > 1:
            self.split_info.value = f"({self.split_count} Kişi) Başı: {money(total / self.split_count)}"
        else:
            self.split_info.value = ""
            
        for x in rows:
            self.orders.controls.append(ft.Container(padding=7,border_radius=8,bgcolor=ft.colors.with_opacity(.05,ft.colors.PRIMARY),content=ft.Row([ft.Column([ft.Text(x["product_name"],weight=ft.FontWeight.BOLD),ft.Text(f"{x['quantity']}x {money(x['unit_price'])}"),ft.Text(f"📝 {x['note']}" if x["note"] else "",size=10,color=ft.colors.GREY_600)],expand=True),ft.IconButton(ft.icons.EDIT_NOTE,on_click=lambda e,rr=x:self.edit_note(rr)),ft.IconButton(ft.icons.REMOVE_CIRCLE_OUTLINE,icon_color=ft.colors.RED_500,on_click=lambda e,i=x["id"]:self.remove(i))])))
        with db_cursor(commit=True) as c:
            c.execute("UPDATE tables SET status=%s,total_amount=%s,staff_name=%s,start_time=COALESCE(start_time,%s) WHERE id=%s",("occupied" if total else "empty",total,self.user,now_str(),self.table_id) if total else ("empty",0,None,None,self.table_id))
        self.update_page()
        
    def render_products(self):
        self.products.controls=[]
        with db_cursor() as c:
            q="SELECT * FROM products WHERE is_active=1"
            args=[]
            if self.cat!="Tümü":
                q+=" AND category=%s"
                args=[self.cat]
            q += " ORDER BY category,name"
            c.execute(q,args)
            for p in c.fetchall():self.products.controls.append(ft.ListTile(title=ft.Text(p["name"],weight=ft.FontWeight.BOLD),subtitle=ft.Text(money(p["price"])),trailing=ft.IconButton(ft.icons.ADD_CIRCLE,icon_color="#10B981",on_click=lambda e,rr=p:self.add(rr))))
        self.update_page()
    def add(self,p):
        with db_cursor(commit=True) as c:
            c.execute("SELECT id FROM order_items WHERE table_id=%s AND product_id=%s AND COALESCE(note,'')=''",(self.table_id,p["id"]));x=c.fetchone()
            if x:c.execute("UPDATE order_items SET quantity=quantity+1 WHERE id=%s",(x["id"],))
            else:c.execute("INSERT INTO order_items(table_id,product_id,product_name,quantity,unit_price) VALUES(%s,%s,%s,%s,%s)",(self.table_id,p["id"],p["name"],1,p["price"]))
        play_sound(self.page, "click");self.refresh()
    def remove(self,i):
        with db_cursor(commit=True) as c:
            c.execute("SELECT quantity FROM order_items WHERE id=%s",(i,));x=c.fetchone()
            if x and x["quantity"]>1:c.execute("UPDATE order_items SET quantity=quantity-1 WHERE id=%s",(i,))
            else:c.execute("DELETE FROM order_items WHERE id=%s",(i,))
        self.refresh()
    def edit_note(self,x):
        note=ft.TextField(label="Garson sipariş notu",value=x["note"] or "",multiline=True)
        def save(e):
            with db_cursor(commit=True) as c:c.execute("UPDATE order_items SET note=%s WHERE id=%s",(note.value,i));close_dlg(self.page,d);self.refresh()
        i=x["id"];d=ft.AlertDialog(title=ft.Text(x["product_name"]),content=note,actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
        
    def quick_pay(self, pay_type):
        with db_cursor() as c:
            c.execute("SELECT * FROM order_items WHERE table_id=%s",(self.table_id,))
            items=c.fetchall()
            total=sum(x["quantity"]*x["unit_price"] for x in items)
        if total<=0: return
        
        with db_cursor(commit=True) as c:
            total_cost=0
            for it in items:
                ok,msg=check_stock(c,it["product_id"],it["quantity"])
                if not ok and self.recipe_exists(c,it["product_id"]):
                    snackbar(self.page,msg,ft.colors.RED_600);return
            for it in items:total_cost+=deduct_stock(c,it["product_id"],it["quantity"],self.user,f"Masa {self.table_name}")
            
            cashv = total if pay_type == "Nakit" else 0
            cardv = total if pay_type == "Kredi Kartı" else 0
            
            comm = calc_commission("Masa", total, cardv)
            p = total - total_cost - comm
            
            summary=", ".join(f"{x['quantity']}x {x['product_name']}" for x in items)
            
            c.execute("INSERT INTO sales(table_name,order_source,total_amount,total_cost,commission_amount,profit_amount,payment_type,cash_amount,card_amount,items_summary,staff_name,created_at,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",(self.table_name,"Masa",total,total_cost,comm,p,pay_type,cashv,cardv,summary,self.user,now_str(),"completed"))
            sid=c.fetchone()["id"]
            for it in items:c.execute("INSERT INTO sale_items(sale_id,product_id,product_name,quantity,unit_price,total_price,unit_cost) VALUES(%s,%s,%s,%s,%s,%s,%s)",(sid,it["product_id"],it["product_name"],it["quantity"],it["unit_price"],it["quantity"]*it["unit_price"],calculate_recipe_cost(c,it["product_id"],1)))
            c.execute("DELETE FROM order_items WHERE table_id=%s",(self.table_id,))
            c.execute("UPDATE tables SET status='empty',total_amount=0,start_time=NULL,staff_name=NULL WHERE id=%s",(self.table_id,))
        audit(self.user,"SATIŞ",f"Masa {self.table_name} {money(total)}")
        play_sound(self.page, "success")
        if hasattr(self, "page") and self.page: self.page.go("/tables")
        
    def payment(self,e):
        with db_cursor() as c:
            c.execute("SELECT * FROM order_items WHERE table_id=%s",(self.table_id,))
            items=c.fetchall()
            total=sum(x["quantity"]*x["unit_price"] for x in items)
        if total<=0:return
        disc=ft.TextField(label="İndirim (TL)",value="0");cash=ft.TextField(label="Nakit (TL)",value=str(total));card=ft.TextField(label="Kart (TL)",value="0")
        def finish(e):
            discount=min(max(num(disc.value),0),total)
            cashv=max(num(cash.value),0)
            cardv=max(num(card.value),0)
            final=total-discount
            if abs(cashv+cardv-final)>0.01:
                snackbar(self.page,f"Ödeme toplamı {money(final)} olmalı",ft.colors.RED_600);return
            with db_cursor(commit=True) as c:
                total_cost=0
                for it in items:
                    ok,msg=check_stock(c,it["product_id"],it["quantity"])
                    if not ok and self.recipe_exists(c,it["product_id"]):
                        snackbar(self.page,msg,ft.colors.RED_600);return
                for it in items:total_cost+=deduct_stock(c,it["product_id"],it["quantity"],self.user,f"Masa {self.table_name}")
                
                comm = calc_commission("Masa", final, cardv)
                p = final - total_cost - comm
                
                pay="Parçalı" if cashv>0 and cardv>0 else ("Nakit" if cashv>0 else "Kart")
                summary=", ".join(f"{x['quantity']}x {x['product_name']}" for x in items)
                
                c.execute("INSERT INTO sales(table_name,order_source,total_amount,total_cost,commission_amount,profit_amount,payment_type,cash_amount,card_amount,items_summary,staff_name,created_at,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",(self.table_name,"Masa",final,total_cost,comm,p,pay,cashv,cardv,summary,self.user,now_str(),"completed"))
                sid=c.fetchone()["id"]
                for it in items:c.execute("INSERT INTO sale_items(sale_id,product_id,product_name,quantity,unit_price,total_price,unit_cost) VALUES(%s,%s,%s,%s,%s,%s,%s)",(sid,it["product_id"],it["product_name"],it["quantity"],it["unit_price"],it["quantity"]*it["unit_price"],calculate_recipe_cost(c,it["product_id"],1)))
                c.execute("DELETE FROM order_items WHERE table_id=%s",(self.table_id,))
                c.execute("UPDATE tables SET status='empty',total_amount=0,start_time=NULL,staff_name=NULL WHERE id=%s",(self.table_id,))
            audit(self.user,"SATIŞ",f"Masa {self.table_name} {money(final)}")
            play_sound(self.page, "success"); close_dlg(self.page,d); 
            if hasattr(self, "page") and self.page: self.page.go("/tables")
        d=ft.AlertDialog(title=ft.Text(f"Parçalı Ödeme & İndirim • {money(total)}"),content=ft.Column([disc,cash,card],tight=True),actions=[ft.TextButton("İptal",on_click=lambda e:close_dlg(self.page,d)),ft.ElevatedButton("Ödemeyi Tamamla",on_click=finish)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
        
    def recipe_exists(self,c,pid):
        c.execute("SELECT 1 FROM recipes WHERE product_id=%s LIMIT 1",(pid,));return c.fetchone() is not None
        
    def transfer(self,e):
        with db_cursor() as c:c.execute("SELECT id,name,status FROM tables WHERE id!=%s ORDER BY name",(self.table_id,));rows=c.fetchall()
        dd=ft.Dropdown(label="Hedef masa",options=[ft.dropdown.Option(str(x["id"]),f"{x['name']} ({'Dolu' if x['status']=='occupied' else 'Boş'})") for x in rows])
        def do(e):
            if not dd.value:return
            target=int(dd.value)
            with db_cursor(commit=True) as c:
                c.execute("SELECT status FROM tables WHERE id=%s",(target,));st=c.fetchone()["status"]
                if st=="occupied":
                    c.execute("SELECT product_id,product_name,quantity,unit_price,note FROM order_items WHERE table_id=%s",(self.table_id,));src=c.fetchall()
                    for x in src:
                        c.execute("SELECT id FROM order_items WHERE table_id=%s AND product_id=%s AND note=%s",(target,x["product_id"],x["note"] or ""));ex=c.fetchone()
                        if ex:c.execute("UPDATE order_items SET quantity=quantity+%s WHERE id=%s",(x["quantity"],ex["id"]))
                        else:c.execute("INSERT INTO order_items(table_id,product_id,product_name,quantity,unit_price,note) VALUES(%s,%s,%s,%s,%s,%s)",(target,x["product_id"],x["product_name"],x["quantity"],x["unit_price"],x["note"] or ""))
                else:c.execute("UPDATE order_items SET table_id=%s WHERE table_id=%s",(target,self.table_id))
                c.execute("SELECT COALESCE(SUM(quantity*unit_price),0) t FROM order_items WHERE table_id=%s",(target,));tot=c.fetchone()["t"];c.execute("UPDATE tables SET status='occupied',total_amount=%s,staff_name=%s WHERE id=%s",(tot,self.user,target));c.execute("DELETE FROM order_items WHERE table_id=%s",(self.table_id,));c.execute("UPDATE tables SET status='empty',total_amount=0,start_time=NULL,staff_name=NULL WHERE id=%s",(self.table_id,))
            close_dlg(self.page,d);
            if hasattr(self, "page") and self.page: self.page.go("/tables")
        d=ft.AlertDialog(title=ft.Text("Masa taşı / birleştir"),content=dd,actions=[ft.ElevatedButton("Aktar",on_click=do)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

# =============================================================
# GEL-AL / PAKET & BEKLEYEN KUYRUK
# =============================================================

class QuickPOSView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/quick-pos")
        self.cart={}
        with db_cursor() as c:
            c.execute("SELECT id, name FROM couriers WHERE is_active=1")
            self.couriers = c.fetchall()

        self.ch_dd = ft.Dropdown(label="Kanal", options=[ft.dropdown.Option(x) for x in ["Gel-Al", "Paket Servis", "Trendyol", "Yemeksepeti", "Getir"]], value="Gel-Al", width=140, on_change=self.on_ch_change)
        self.courier_dd = ft.Dropdown(label="Kurye", options=[ft.dropdown.Option(str(c['id']), c['name']) for c in self.couriers], visible=False)
        self.cust_name = ft.TextField(label="Müşteri Adı / Tel", height=40)
        
        self.list = ft.Column(scroll=ft.ScrollMode.ADAPTIVE, expand=True)
        self.total = ft.Text("₺0.00", size=20, weight=ft.FontWeight.BOLD, color="#059669")
        self.prods_grid = ft.GridView(expand=True, runs_count=2, max_extent=140, spacing=8, run_spacing=8)
        self.queue_col = ft.Column(scroll=ft.ScrollMode.ADAPTIVE, expand=True)

        header_row = ft.Row([ft.IconButton(ft.icons.ARROW_BACK, on_click=lambda e: e.page.go("/dashboard")), ft.Text("Hızlı Satış & Paket", size=18, weight=ft.FontWeight.BOLD), ft.Row([self.ch_dd, self.courier_dd])], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        left_p = ft.Container(expand=3, content=ft.Column([ft.Text("Menü", weight=ft.FontWeight.BOLD), self.prods_grid]))
        
        mid_c = ft.Container(expand=2, border=ft.border.all(1, ft.colors.OUTLINE_VARIANT), border_radius=12, padding=10, content=ft.Column([
            ft.Row([ft.Text("Sepet", weight=ft.FontWeight.BOLD), ft.TextButton("Temizle", on_click=lambda _: self.clear())], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            self.cust_name, self.list, ft.Divider(), ft.Row([ft.Text("Toplam:"), self.total], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Text("Öde & Kapat:", size=11, weight=ft.FontWeight.BOLD),
            ft.Row([
                ft.ElevatedButton("💵 NAKİT", bgcolor="#10B981", color=ft.colors.WHITE, expand=True, on_click=lambda _: self.finish("Nakit", "completed")),
                ft.ElevatedButton("💳 KART", bgcolor="#3B82F6", color=ft.colors.WHITE, expand=True, on_click=lambda _: self.finish("Kredi Kartı", "completed")),
            ]),
            ft.Row([
                ft.ElevatedButton("🌐 Online", bgcolor="#8B5CF6", color=ft.colors.WHITE, expand=True, on_click=lambda _: self.finish("Online Ödeme", "completed")),
                ft.ElevatedButton("⏳ Beklet", bgcolor="#F59E0B", color=ft.colors.WHITE, expand=True, on_click=lambda _: self.finish("Belirsiz", "preparing"))
            ])
        ]))

        right_q = ft.Container(width=220, border=ft.border.all(1, ft.colors.OUTLINE_VARIANT), border_radius=12, padding=8, content=ft.Column([ft.Text("⏳ Bekleyen Kuyruk", weight=ft.FontWeight.BOLD, color="#F59E0B"), self.queue_col]))

        self.controls = [header_row, ft.Row([left_p, mid_c, right_q], expand=True)]
        self.load_p()
        self.load_q()

    def on_ch_change(self, e):
        self.courier_dd.visible = self.ch_dd.value != "Gel-Al"
        self.update_page()

    def load_p(self):
        with db_cursor() as c:
            c.execute("SELECT * FROM products WHERE is_active=1 ORDER BY category,name")
            for p in c.fetchall():
                self.prods_grid.controls.append(ft.Container(bgcolor=ft.colors.with_opacity(0.06, "#EC4899"), border=ft.border.all(1, ft.colors.OUTLINE_VARIANT), border_radius=10, padding=8, content=ft.Column([ft.Text(p["name"], weight=ft.FontWeight.BOLD, size=12, text_align=ft.TextAlign.CENTER), ft.Text(money(p['price']), size=12, color="#EC4899")], alignment=ft.MainAxisAlignment.CENTER), on_click=lambda _, prod=p: self.add(prod)))

    def add(self, p):
        play_sound(self.page, "click")
        pid = p["id"]
        if pid in self.cart: self.cart[pid]["qty"] += 1
        else: self.cart[pid] = {"name": p["name"], "price": p["price"], "qty": 1}
        self.render()

    def clear(self): self.cart.clear(); self.render()

    def render(self):
        self.list.controls.clear()
        tot = sum(i["qty"] * i["price"] for i in self.cart.values())
        for pid, i in self.cart.items():
            self.list.controls.append(ft.Row([ft.Text(f"{i['qty']}x {i['name']}", size=12, expand=True), ft.Text(money(i['qty']*i['price']), size=12), ft.IconButton(ft.icons.REMOVE_CIRCLE, icon_size=18, icon_color=ft.colors.RED_500, on_click=lambda _, p=pid: self.sub(p))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN))
        self.total.value = money(tot)
        self.update_page()

    def sub(self, p):
        if self.cart[p]["qty"] > 1: self.cart[p]["qty"] -= 1
        else: del self.cart[p]
        self.render()

    def finish(self, pay_type, status):
        if not self.cart: return
        tot = sum(i["qty"] * i["price"] for i in self.cart.values())
        
        cash_v = tot if pay_type == "Nakit" else 0
        card_v = tot if pay_type == "Kredi Kartı" else 0
        
        total_cost = 0.0
        comm = 0.0
        
        with db_cursor(commit=True) as c:
            if status == "completed":
                for pid, i in self.cart.items():
                    ok,msg=check_stock(c,pid,i["qty"])
                    if not ok and self.recipe_exists(c,pid):snackbar(self.page,msg,ft.colors.RED_600);return
                for pid, i in self.cart.items():
                    total_cost += deduct_stock(c,pid,i["qty"],self.user,"Gel-Al/Paket")
                
                comm = calc_commission(self.ch_dd.value, tot, card_v)
            
            profit = tot - total_cost - comm
            summ = ", ".join([f"{i['qty']}x {i['name']}" for i in self.cart.values()])
            c_id = self.courier_dd.value if self.ch_dd.value != "Gel-Al" else None
            
            c.execute("""
                INSERT INTO sales (table_name, order_source, total_amount, total_cost, commission_amount, profit_amount, payment_type, cash_amount, card_amount, items_summary, staff_name, customer_name, courier_id, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, ("Paket/Kuyruk", self.ch_dd.value, tot, total_cost, comm, profit, pay_type, cash_v, card_v, summ, self.user, self.cust_name.value, c_id, status, now_str()))
            
            s_id = c.fetchone()["id"]
            for pid, i in self.cart.items():
                c.execute("INSERT INTO sale_items (sale_id, product_id, product_name, quantity, unit_price, total_price) VALUES (%s, %s, %s, %s, %s, %s)", (s_id, pid, i["name"], i["qty"], i["price"], i["qty"]*i["price"]))

        play_sound(self.page, "success"); self.clear(); self.cust_name.value = ""; self.load_q()

    def load_q(self):
        self.queue_col.controls.clear()
        with db_cursor() as c:
            c.execute("SELECT id, customer_name, order_source, total_amount FROM sales WHERE status='preparing' ORDER BY id ASC")
            for q in c.fetchall():
                self.queue_col.controls.append(
                    ft.Container(bgcolor=ft.colors.with_opacity(0.1, "#F59E0B"), border_radius=8, padding=6, content=ft.Column([
                        ft.Text(f"👤 {q['customer_name'] or 'İsimsiz'}", weight=ft.FontWeight.BOLD, size=12),
                        ft.Text(f"{q['order_source']} - {money(q['total_amount'])}", size=11),
                        ft.Row([ft.ElevatedButton("Nakit Al", height=25, on_click=lambda _, sid=q['id']: self.close_q(sid, "Nakit")), ft.ElevatedButton("Kart Al", height=25, on_click=lambda _, sid=q['id']: self.close_q(sid, "Kredi Kartı"))])
                    ]))
                )
        self.update_page()

    def close_q(self, sid, p_type):
        with db_cursor(commit=True) as c:
            c.execute("SELECT total_amount, order_source FROM sales WHERE id=%s", (sid,))
            s_row = c.fetchone()
            tot = s_row["total_amount"]
            source = s_row["order_source"]
            
            c.execute("SELECT product_id, quantity FROM sale_items WHERE sale_id=%s", (sid,))
            items = c.fetchall()
            for i in items:
                ok,msg=check_stock(c,i['product_id'],i['quantity'])
                if not ok and self.recipe_exists(c,i['product_id']):snackbar(self.page,msg,ft.colors.RED_600);return
            t_cost=0
            for i in items: t_cost += deduct_stock(c,i['product_id'],i['quantity'],self.user,"Kuyruk Tamamlandı")
            
            card_v = tot if p_type == "Kredi Kartı" else 0
            comm = calc_commission(source, tot, card_v)
            prof = tot - t_cost - comm

            c.execute("UPDATE sales SET status='completed', payment_type=%s, cash_amount=%s, card_amount=%s, commission_amount=%s, total_cost=%s, profit_amount=%s WHERE id=%s", (p_type, tot if p_type=="Nakit" else 0, card_v, comm, t_cost, prof, sid))
        play_sound(self.page, "success"); self.load_q()
        
    def recipe_exists(self,c,pid):
        c.execute("SELECT 1 FROM recipes WHERE product_id=%s LIMIT 1",(pid,));return c.fetchone() is not None

# =============================================================
# KASA / CARİ / VARDİYA / Z RAPORU
# =============================================================

class CashView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/cash");self.trans=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.acc=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Kasa & Cari"),ft.Tabs(expand=True,tabs=[ft.Tab(text="Kasa",content=ft.Column([ft.ElevatedButton("+ İşlem",on_click=self.add_transaction),self.trans],expand=True)),ft.Tab(text="Cari",content=ft.Column([ft.ElevatedButton("+ Cari",on_click=self.add_customer),self.acc],expand=True))])];self.refresh()
    def refresh(self):self.load_trans();self.load_acc()
    def load_trans(self):
        self.trans.controls=[]
        with db_cursor() as c:c.execute("SELECT * FROM cash_transactions ORDER BY id DESC LIMIT 100");rows=c.fetchall()
        for x in rows:self.trans.controls.append(ft.ListTile(leading=ft.Icon(ft.icons.ARROW_DOWNWARD if x["transaction_type"]=="in" else ft.icons.ARROW_UPWARD,color="#10B981" if x["transaction_type"]=="in" else ft.colors.RED_600),title=ft.Text(f"{x['category']} • {money(x['amount'])}"),subtitle=ft.Text(f"{x['description'] or ''} • {x['created_at']}")))
        self.update_page()
    def add_transaction(self,e):
        typ=ft.Dropdown(label="Tip",value="out",options=[ft.dropdown.Option("out","Masraf"),ft.dropdown.Option("in","Giriş")]);cat=ft.TextField(label="Kategori");amt=ft.TextField(label="Tutar (TL)");desc=ft.TextField(label="Açıklama")
        def save(e):
            with db_cursor(commit=True) as c:c.execute("INSERT INTO cash_transactions(transaction_type,amount,category,description,staff_name,created_at) VALUES(%s,%s,%s,%s,%s,%s)",(typ.value,num(amt.value),cat.value,desc.value,self.user,now_str()))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Kasa işlemi"),content=ft.Column([typ,cat,amt,desc],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def load_acc(self):
        self.acc.controls=[]
        with db_cursor() as c:c.execute("SELECT * FROM customer_accounts ORDER BY current_balance DESC");rows=c.fetchall()
        for x in rows:self.acc.controls.append(ft.ListTile(title=ft.Text(x["full_name"]),subtitle=ft.Text(f"{x['phone'] or ''} • {x['account_type']}"),trailing=ft.Row([ft.Text(money(x["current_balance"]),color=ft.colors.RED_600 if x["current_balance"]>0 else "#059669"),ft.ElevatedButton("Tahsil",on_click=lambda e,rr=x:self.collect(rr))])))
        self.update_page()
    def add_customer(self,e):
        n=ft.TextField(label="Ad Soyad");ph=ft.TextField(label="Telefon");typ=ft.Dropdown(label="Tür",value="Müdavim",options=[ft.dropdown.Option("Müdavim"),ft.dropdown.Option("Personel"),ft.dropdown.Option("Firma")])
        def save(e):
            with db_cursor(commit=True) as c:c.execute("INSERT INTO customer_accounts(full_name,phone,account_type,created_at) VALUES(%s,%s,%s,%s)",(n.value,ph.value,typ.value,now_str()))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Cari"),content=ft.Column([n,ph,typ],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def collect(self,x):
        amt=ft.TextField(label="Tahsilat",value=str(x["current_balance"]))
        def save(e):
            v=min(max(num(amt.value),0),x["current_balance"])
            with db_cursor(commit=True) as c:c.execute("UPDATE customer_accounts SET current_balance=current_balance-%s WHERE id=%s",(v,x["id"]));c.execute("INSERT INTO cash_transactions(transaction_type,amount,category,description,staff_name,created_at) VALUES('in',%s,%s,%s,%s,%s)",(v,"Veresiye tahsilat",x["full_name"],self.user,now_str()))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Tahsilat"),content=amt,actions=[ft.ElevatedButton("Tamamla",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

class ShiftsView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/shifts");self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Vardiya & Z Raporu"),ft.Row([ft.ElevatedButton("Vardiya Aç",on_click=self.open_shift),ft.ElevatedButton("Açık Vardiyayı Kapat",on_click=self.close_shift),ft.ElevatedButton("Z Raporu",on_click=self.z_report)]),self.list_col];self.refresh()
    def refresh(self):
        self.list_col.controls=[]
        with db_cursor() as c:c.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 30");rows=c.fetchall()
        for x in rows:self.list_col.controls.append(ft.ListTile(title=ft.Text(f"{x['staff_name']} • {x['status']}"),subtitle=ft.Text(f"Açılış {x['opened_at']} • Kapanış {x['closed_at'] or '-'}"),trailing=ft.Text(money(x["closing_cash"] if x["closing_cash"] is not None else x["opening_cash"]))))
        self.update_page()
    def open_shift(self,e):
        with db_cursor() as c:
            c.execute("SELECT 1 FROM shifts WHERE status='open' AND staff_name=%s",(self.user,))
            already_open=c.fetchone()
        pg=self._pg(e)
        if already_open:
            if pg: snackbar(pg,"Zaten açık bir vardiyanız var.")
            return
        cash=ft.TextField(label="Açılış kasası (TL)")
        def save(ev):
            with db_cursor(commit=True) as c:c.execute("INSERT INTO shifts(staff_name,opened_at,opening_cash,status) VALUES(%s,%s,%s,'open')",(self.user,now_str(),num(cash.value)))
            p=self._pg(ev) or pg
            if p: p.close(d)
            self.refresh()
        d=ft.AlertDialog(title=ft.Text("Vardiya aç"),content=cash,actions=[ft.ElevatedButton("Aç",on_click=save)])
        if pg: pg.open(d)
        else: print("UYARI: open_shift() için geçerli bir page bulunamadı.")
    def close_shift(self,e):
        pg=self._pg(e)
        with db_cursor() as c:c.execute("SELECT * FROM shifts WHERE status='open' AND staff_name=%s ORDER BY id DESC LIMIT 1",(self.user,));s=c.fetchone()
        if not s:
            if pg: snackbar(pg,"Açık vardiya yok")
            return
        with db_cursor() as c:c.execute("SELECT COALESCE(SUM(cash_amount),0) cash FROM sales WHERE SUBSTRING(created_at,1,10)=%s AND staff_name=%s AND status='completed'",(today_str(),self.user));sales_cash=c.fetchone()["cash"];c.execute("SELECT COALESCE(SUM(CASE WHEN transaction_type='in' THEN amount ELSE -amount END),0) net FROM cash_transactions WHERE SUBSTRING(created_at,1,10)=%s AND staff_name=%s",(today_str(),self.user));net=c.fetchone()["net"]
        expected=s["opening_cash"]+sales_cash+net
        actual=ft.TextField(label="Kapanış kasası",value=str(expected));note=ft.TextField(label="Not")
        def save(ev):
            with db_cursor(commit=True) as c:c.execute("UPDATE shifts SET closed_at=%s,closing_cash=%s,expected_cash=%s,status='closed',note=%s WHERE id=%s",(now_str(),num(actual.value),expected,note.value,s["id"]))
            p=self._pg(ev) or pg
            if p: p.close(d)
            self.refresh()
        d=ft.AlertDialog(title=ft.Text(f"Vardiya kapat • Beklenen {money(expected)}"),content=ft.Column([actual,note],tight=True),actions=[ft.ElevatedButton("Kapat",on_click=save)])
        if pg: pg.open(d)
        else: print("UYARI: close_shift() için geçerli bir page bulunamadı.")
    def z_report(self,e):
        pg=self._pg(e)
        with db_cursor() as c:c.execute("SELECT COALESCE(SUM(total_amount),0) rev,COALESCE(SUM(profit_amount),0) prof,COUNT(*) n FROM sales WHERE SUBSTRING(created_at,1,10)=%s AND status='completed'",(today_str(),));r=c.fetchone();c.execute("SELECT COALESCE(SUM(cash_amount),0) cash,COALESCE(SUM(card_amount),0) card FROM sales WHERE SUBSTRING(created_at,1,10)=%s AND status='completed'",(today_str(),));p=c.fetchone();
        d=ft.AlertDialog(title=ft.Text("Z Raporu • "+today_str()),content=ft.Column([ft.Text(f"Satış: {r['n']}"),ft.Text(f"Ciro: {money(r['rev'])}"),ft.Text(f"Net Kâr: {money(r['prof'])}"),ft.Text(f"Nakit: {money(p['cash'])}"),ft.Text(f"Kart: {money(p['card'])}")],tight=True),actions=[ft.TextButton("Kapat",on_click=lambda ev:(self._pg(ev) or pg).close(d) if (self._pg(ev) or pg) else None)])
        if pg: pg.open(d)
        else: print("UYARI: z_report() için geçerli bir page bulunamadı.")

# =============================================================
# TEDARİK / SATIN ALMA
# =============================================================

class SuppliersView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/suppliers");self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Tedarikçi & Satın Alma"),ft.Row([ft.ElevatedButton("+ Tedarikçi",on_click=self.add_supplier),ft.ElevatedButton("+ Satın Alma",on_click=self.purchase)]),self.list_col];self.refresh()
    def refresh(self):
        self.list_col.controls=[]
        with db_cursor() as c:c.execute("SELECT * FROM suppliers ORDER BY name");rows=c.fetchall()
        for x in rows:self.list_col.controls.append(ft.ListTile(title=ft.Text(x["name"]),subtitle=ft.Text(f"{x['phone'] or ''} • {x['note'] or ''}"),trailing=ft.Text("Aktif" if x["is_active"] else "Pasif")))
        self.update_page()
    def add_supplier(self,e):
        n=ft.TextField(label="Tedarikçi");ph=ft.TextField(label="Telefon");note=ft.TextField(label="Not")
        def save(e):
            with db_cursor(commit=True) as c:c.execute("INSERT INTO suppliers(name,phone,note) VALUES(%s,%s,%s)",(n.value,ph.value,note.value))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Tedarikçi"),content=ft.Column([n,ph,note],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
    def purchase(self,e):
        with db_cursor() as c:c.execute("SELECT id,name,unit FROM raw_materials ORDER BY name");rms=c.fetchall();c.execute("SELECT id,name FROM suppliers WHERE is_active=1 ORDER BY name");sup=c.fetchall()
        s=ft.Dropdown(label="Tedarikçi",options=[ft.dropdown.Option(str(x["id"]),x["name"]) for x in sup]);rm=ft.Dropdown(label="Hammadde",options=[ft.dropdown.Option(str(x["id"]),f"{x['name']} ({x['unit']})") for x in rms]);q=ft.TextField(label="Miktar");cost=ft.TextField(label="Birim maliyet");inv=ft.TextField(label="Fatura no")
        def save(e):
            qty=num(q.value);unitcost=num(cost.value);rr=next((x for x in rms if str(x["id"])==rm.value),None)
            if not rr or qty<=0:return
            with db_cursor(commit=True) as c:
                total=qty*unitcost
                c.execute("INSERT INTO purchases(supplier_id,invoice_no,total_amount,staff_name,created_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",(int(s.value) if s.value else None,inv.value,total,self.user,now_str()))
                pid=c.fetchone()["id"]
                c.execute("INSERT INTO purchase_items(purchase_id,raw_material_id,quantity,unit_cost,total_cost) VALUES(%s,%s,%s,%s,%s)",(pid,rr["id"],qty,unitcost,total))
                c.execute("SELECT current_stock FROM raw_materials WHERE id=%s",(rr["id"],))
                before=c.fetchone()["current_stock"];after=before+qty
                c.execute("UPDATE raw_materials SET current_stock=%s,unit_cost=%s WHERE id=%s",(after,unitcost,rr["id"]))
                c.execute("INSERT INTO stock_movements(raw_material_id,movement_type,quantity,before_stock,after_stock,unit_cost,reference,staff_name,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(rr["id"],"SATIN ALMA",qty,before,after,unitcost,f"Fatura {inv.value}",self.user,now_str()))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Satın alma"),content=ft.Column([s,rm,q,cost,inv],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

# =============================================================
# REZERVASYON / SADAKAT / KAMPANYA
# =============================================================

class ReservationsView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/reservations");self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Rezervasyonlar"),ft.ElevatedButton("+ Rezervasyon",on_click=self.add),self.list_col];self.refresh()
    def refresh(self):
        self.list_col.controls=[]
        with db_cursor() as c:c.execute("SELECT r.*,t.name table_name FROM reservations r LEFT JOIN tables t ON t.id=r.table_id ORDER BY reservation_time DESC");rows=c.fetchall()
        for x in rows:self.list_col.controls.append(ft.ListTile(title=ft.Text(f"{x['customer_name']} • {x['party_size']} kişi"),subtitle=ft.Text(f"{x['reservation_time']} • {x['table_name'] or 'Masa yok'} • {x['note'] or ''}"),trailing=ft.Dropdown(width=130,value=x["status"],options=[ft.dropdown.Option(y) for y in ["Bekliyor","Geldi","İptal","Tamamlandı"]],on_change=lambda e,i=x["id"]:self.status(i,e.control.value))))
        self.update_page()
    def status(self,i,v):
        with db_cursor(commit=True) as c:c.execute("UPDATE reservations SET status=%s WHERE id=%s",(v,i));self.refresh()
    def add(self,e):
        n=ft.TextField(label="Müşteri");ph=ft.TextField(label="Telefon");dt=ft.TextField(label="Tarih / saat",value=datetime.now().strftime("%Y-%m-%d %H:%M"));party=ft.TextField(label="Kişi",value="2");note=ft.TextField(label="Not")
        with db_cursor() as c:c.execute("SELECT id,name FROM tables ORDER BY name");tabs=c.fetchall()
        table=ft.Dropdown(label="Masa",options=[ft.dropdown.Option(str(x["id"]),x["name"]) for x in tabs])
        def save(e):
            with db_cursor(commit=True) as c:c.execute("INSERT INTO reservations(customer_name,phone,table_id,reservation_time,party_size,status,note) VALUES(%s,%s,%s,%s,%s,'Bekliyor',%s)",(n.value,ph.value,int(table.value) if table.value else None,dt.value,int(num(party.value,2)),note.value))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Rezervasyon"),content=ft.Column([n,ph,dt,party,table,note],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

class LoyaltyView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/loyalty");self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Müşteri & Sadakat"),ft.ElevatedButton("+ Müşteri",on_click=self.add),self.list_col];self.refresh()
    def refresh(self):
        self.list_col.controls=[]
        with db_cursor() as c:c.execute("SELECT a.*,COALESCE(l.points,0) points,COALESCE(l.total_spend,0) spend,COALESCE(l.visit_count,0) visits FROM customer_accounts a LEFT JOIN loyalty l ON l.customer_id=a.id ORDER BY spend DESC");rows=c.fetchall()
        for x in rows:self.list_col.controls.append(ft.ListTile(title=ft.Text(x["full_name"]),subtitle=ft.Text(f"{x['phone'] or ''} • {x['visits']} ziyaret • {money(x['spend'])}"),trailing=ft.Text(f"⭐ {x['points']} puan",weight=ft.FontWeight.BOLD)))
        self.update_page()
    def add(self,e):
        n=ft.TextField(label="Ad Soyad");ph=ft.TextField(label="Telefon")
        def save(e):
            with db_cursor(commit=True) as c:
                c.execute("INSERT INTO customer_accounts(full_name,phone,account_type,created_at) VALUES(%s,%s,%s,%s) RETURNING id",(n.value,ph.value,"Müdavim",now_str()))
                cid=c.fetchone()["id"]
                c.execute("INSERT INTO loyalty(customer_id) VALUES(%s)",(cid,))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Müşteri"),content=ft.Column([n,ph],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

class CampaignsView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/campaigns");self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Kampanya & İndirim"),ft.ElevatedButton("+ Kampanya",on_click=self.add),self.list_col];self.refresh()
    def refresh(self):
        self.list_col.controls=[]
        with db_cursor() as c:c.execute("SELECT * FROM campaigns ORDER BY id DESC");rows=c.fetchall()
        for x in rows:self.list_col.controls.append(ft.ListTile(title=ft.Text(x["name"]),subtitle=ft.Text(f"%{x['discount_percent']} • Min {money(x['min_total'])} • {x['description'] or ''}"),trailing=ft.Switch(value=bool(x["active"]),on_change=lambda e,i=x["id"]:self.toggle(i,e.control.value))))
        self.update_page()
    def toggle(self,i,v):
        with db_cursor(commit=True) as c:c.execute("UPDATE campaigns SET active=%s WHERE id=%s",(1 if v else 0,i))
    def add(self,e):
        n=ft.TextField(label="Kampanya adı");desc=ft.TextField(label="Açıklama");pct=ft.TextField(label="İndirim %",value="10");minv=ft.TextField(label="Minimum sepet")
        def save(e):
            with db_cursor(commit=True) as c:c.execute("INSERT INTO campaigns(name,description,discount_percent,min_total,active) VALUES(%s,%s,%s,%s,1)",(n.value,desc.value,num(pct.value),num(minv.value)))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Kampanya"),content=ft.Column([n,desc,pct,minv],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

# =============================================================
# RAPORLAR 
# =============================================================

class ReportsView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/reports");self.list_col=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True);self.controls=[header(page,"Analiz Kokpiti"),ft.Row([ft.ElevatedButton("Bugün",on_click=lambda e:self.load(0)),ft.ElevatedButton("7 Gün",on_click=lambda e:self.load(7)),ft.ElevatedButton("30 Gün",on_click=lambda e:self.load(30)),ft.ElevatedButton("CSV Dışa Aktar",on_click=self.export_csv)]),self.list_col];self.load(0)
    def load(self,days):
        self.list_col.controls=[];start=(datetime.now()-timedelta(days=days)).strftime("%Y-%m-%d") if days else today_str()
        with db_cursor() as c:
            c.execute("SELECT COALESCE(SUM(total_amount),0) rev, COALESCE(SUM(total_cost),0) cost, COALESCE(SUM(profit_amount),0) profit, COALESCE(SUM(commission_amount),0) comm, COUNT(*) n FROM sales WHERE SUBSTRING(created_at,1,10)>=%s AND status='completed'",(start,))
            s=c.fetchone()
            c.execute("SELECT product_name,SUM(quantity) qty,SUM(total_price) revenue FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE SUBSTRING(s.created_at,1,10)>=%s AND s.status='completed' GROUP BY product_name ORDER BY qty DESC LIMIT 20",(start,))
            products=c.fetchall()
            c.execute("SELECT staff_name,COUNT(*) n,SUM(total_amount) revenue FROM sales WHERE SUBSTRING(created_at,1,10)>=%s AND status='completed' GROUP BY staff_name ORDER BY revenue DESC",(start,))
            staff=c.fetchall()
            c.execute("SELECT COALESCE(SUM(total_cost),0) cost FROM waste_records WHERE SUBSTRING(created_at,1,10)>=%s",(start,))
            w=c.fetchone()["cost"]
            c.execute("SELECT cr.name, SUM(s.cash_amount) as kn, SUM(s.card_amount) as kk FROM sales s JOIN couriers cr ON s.courier_id=cr.id WHERE SUBSTRING(s.created_at,1,10)>=%s AND s.status='completed' GROUP BY cr.name",(start,))
            c_stats=c.fetchall()
        
        self.list_col.controls=[
            ft.Text(f"Dönem: {start} → bugün",color=ft.colors.GREY_600),
            ft.Row([
                ft.Container(expand=1,padding=12,border_radius=12,bgcolor=ft.colors.with_opacity(.08,"#10B981"),content=ft.Text(f"Ciro\n{money(s['rev'])}",size=15,weight=ft.FontWeight.BOLD)),
                ft.Container(expand=1,padding=12,border_radius=12,bgcolor=ft.colors.with_opacity(.08,"#EF4444"),content=ft.Text(f"Maliyet\n{money(s['cost'])}",size=15,weight=ft.FontWeight.BOLD)),
                ft.Container(expand=1,padding=12,border_radius=12,bgcolor=ft.colors.with_opacity(.08,"#F59E0B"),content=ft.Text(f"Komisyon\n{money(s['comm'])}",size=15,weight=ft.FontWeight.BOLD)),
                ft.Container(expand=1,padding=12,border_radius=12,bgcolor=ft.colors.with_opacity(.08,"#8B5CF6"),content=ft.Text(f"Net Kâr\n{money(s['profit'])}",size=15,weight=ft.FontWeight.BOLD))
            ])
        ]
        
        if c_stats:
            c_str="\n".join([f"🛵 {cs['name']}: Nakit {money(cs['kn'])} | Kart {money(cs['kk'])}" for cs in c_stats])
            self.list_col.controls.append(ft.Text("Kurye Mutabakatı",size=16,weight=ft.FontWeight.BOLD))
            self.list_col.controls.append(ft.Text(c_str))

        self.list_col.controls+=[ft.Text("En çok satanlar",size=16,weight=ft.FontWeight.BOLD)]+[ft.ListTile(title=ft.Text(x["product_name"]),subtitle=ft.Text(f"{x['qty']} adet"),trailing=ft.Text(money(x["revenue"]))) for x in products]+[ft.Text("Personel satışları",size=16,weight=ft.FontWeight.BOLD)]+[ft.ListTile(title=ft.Text(x["staff_name"] or "-"),subtitle=ft.Text(f"{x['n']} satış"),trailing=ft.Text(money(x["revenue"]))) for x in staff]
        self.update_page()
    def export_csv(self,e):
        path=os.path.join(os.path.expanduser("~"),"Desktop","equipos_satis_raporu.csv")
        try:
            os.makedirs(os.path.dirname(path),exist_ok=True)
            with db_cursor() as c:c.execute("SELECT * FROM sales ORDER BY id DESC");rows=c.fetchall()
            with open(path,"w",newline="",encoding="utf-8-sig") as f:
                w=csv.writer(f);w.writerow(rows[0].keys() if rows else ["sales"]);[w.writerow(list(r)) for r in rows]
            snackbar(self.page,f"CSV oluşturuldu: {path}")
        except Exception as ex:snackbar(self.page,str(ex),ft.colors.RED_600)

# =============================================================
# AYARLAR & PERSONEL 
# =============================================================

class SettingsView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/settings")
        self.users_c=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True)
        self.couriers_c=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True)
        
        self.pos_comm = ft.TextField(label="Fiziki POS (Kredi Kartı) Komisyonu (%)", value=get_setting("pos_comm","2.5"))
        self.ty_comm = ft.TextField(label="Trendyol Komisyonu (%)", value=get_setting("trendyol_comm","38"))
        self.ys_comm = ft.TextField(label="Yemeksepeti Komisyonu (%)", value=get_setting("ys_comm","35"))
        self.get_comm = ft.TextField(label="Getir Komisyonu (%)", value=get_setting("getir_comm","38"))
        
        t=ft.Tabs(
            selected_index=0, expand=True,
            tabs=[
                ft.Tab("⚙️ Genel & Komisyon", content=ft.Container(padding=15, content=ft.Column([
                    ft.Text("Platform & POS Kesintileri", weight=ft.FontWeight.BOLD),
                    self.pos_comm, self.ty_comm, self.ys_comm, self.get_comm,
                    ft.ElevatedButton("Komisyon Oranlarını Kaydet", on_click=self.save_comms),
                    ft.Divider(),
                    ft.Switch(label="Ses Efektleri", value=get_setting("sound_enabled","1")=="1", on_change=lambda e:set_setting("sound_enabled","1" if e.control.value else "0")),
                    ft.Switch(label="Masa Sayacı", value=get_setting("timer_enabled","1")=="1", on_change=lambda e:set_setting("timer_enabled","1" if e.control.value else "0"))
                ], scroll=ft.ScrollMode.ADAPTIVE))),
                ft.Tab("👥 Personel", content=ft.Container(padding=15, content=ft.Column([ft.ElevatedButton("+ Personel Ekle", on_click=self.add_u), self.users_c]))),
                ft.Tab("🛵 Kuryeler", content=ft.Container(padding=15, content=ft.Column([ft.ElevatedButton("+ Kurye", on_click=self.add_c), self.couriers_c])))
            ]
        )
        self.controls=[header(page,"Ayarlar & Ekip"), t]
        self.refresh()
        
    def save_comms(self, e):
        set_setting("pos_comm", self.pos_comm.value)
        set_setting("trendyol_comm", self.ty_comm.value)
        set_setting("ys_comm", self.ys_comm.value)
        set_setting("getir_comm", self.get_comm.value)
        snackbar(self.page, "Komisyon oranları başarıyla güncellendi.", ft.colors.GREEN)
        
    def refresh(self):
        self.users_c.controls=[]
        self.couriers_c.controls=[]
        with db_cursor() as c:
            c.execute("SELECT * FROM users ORDER BY username");u_rows=c.fetchall()
            for x in u_rows:
                actions = ft.Container(width=160, content=ft.Row([
                    ft.Switch(value=bool(x["is_active"]), on_change=lambda e,i=x["id"]:self.toggle_u(i,e.control.value)),
                    ft.IconButton(ft.icons.EDIT, icon_color="#3B82F6", tooltip="Düzenle", on_click=lambda e,u=x:self.edit_u_dlg(u)),
                    ft.IconButton(ft.icons.DELETE, icon_color=ft.colors.RED_500, tooltip="Sil", on_click=lambda e,u=x:self.del_u(u), visible=(x["username"]!="yonetici"))
                ], alignment=ft.MainAxisAlignment.END))
                
                self.users_c.controls.append(ft.ListTile(title=ft.Text(x["username"]), subtitle=ft.Text("Aktif" if x["is_active"] else "Pasif"), trailing=actions))
            
            c.execute("SELECT * FROM couriers ORDER BY name");c_rows=c.fetchall()
            for x in c_rows:self.couriers_c.controls.append(ft.ListTile(title=ft.Text(x["name"]),trailing=ft.IconButton(ft.icons.DELETE,icon_color=ft.colors.RED,on_click=lambda e,i=x["id"]:self.del_c(i))))
        self.update_page()
    
    def toggle_u(self,i,v):
        with db_cursor(commit=True) as c:c.execute("UPDATE users SET is_active=%s WHERE id=%s",(1 if v else 0,i))
        
    def add_u(self,e):
        u=ft.TextField(label="Kullanıcı Adı")
        p=ft.TextField(label="PIN (Şifre)",max_length=4,password=True)
        def save(e):
            if not u.value.strip() or not p.value.strip(): return
            try:
                with db_cursor(commit=True) as c:c.execute("INSERT INTO users(username,pin,is_active,created_at) VALUES(%s,%s,%s,%s)",(u.value.strip(),hash_pin(p.value.strip()),1,now_str()))
                close_dlg(self.page,d);self.refresh()
            except psycopg2.IntegrityError:
                snackbar(self.page,"Kullanıcı zaten var!",ft.colors.RED_600)
        d=ft.AlertDialog(title=ft.Text("Personel Ekle"),content=ft.Column([u,p],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

    def edit_u_dlg(self, u_dict):
        u=ft.TextField(label="Kullanıcı Adı", value=u_dict["username"])
        p=ft.TextField(label="Yeni PIN (Aynı kalacaksa boş bırakın)", max_length=4, password=True)
        def save(e):
            new_u = u.value.strip()
            if not new_u: return
            try:
                with db_cursor(commit=True) as c:
                    if p.value.strip():
                        c.execute("UPDATE users SET username=%s, pin=%s WHERE id=%s", (new_u, hash_pin(p.value.strip()), u_dict["id"]))
                    else:
                        c.execute("UPDATE users SET username=%s WHERE id=%s", (new_u, u_dict["id"]))
                close_dlg(self.page,d);self.refresh()
            except psycopg2.IntegrityError:
                snackbar(self.page,"Bu kullanıcı adı zaten sistemde var!",ft.colors.RED_600)
        d=ft.AlertDialog(title=ft.Text("Personel Düzenle"),content=ft.Column([u,p],tight=True),actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)
        
    def del_u(self, u_dict):
        def f():
            with db_cursor(commit=True) as c:c.execute("DELETE FROM users WHERE id=%s",(u_dict["id"],))
            self.refresh()
        self.confirm("Personeli Sil", f"'{u_dict['username']}' adlı personeli sistemden silmek istediğinize emin misiniz?", f, True)

    def del_c(self,cid):
        with db_cursor(commit=True) as c:c.execute("DELETE FROM couriers WHERE id=%s",(cid,))
        self.refresh()
    def add_c(self,e):
        n=ft.TextField(label="Kurye Adı")
        def save(e):
            with db_cursor(commit=True) as c:c.execute("INSERT INTO couriers(name) VALUES(%s)",(n.value,))
            close_dlg(self.page,d);self.refresh()
        d=ft.AlertDialog(title=ft.Text("Kurye Ekle"),content=n,actions=[ft.ElevatedButton("Kaydet",on_click=save)]); 
        if hasattr(self, "page") and self.page: self.page.open(d)

# =============================================================
# METİN TABANLI AI ERP ASİSTANI
# =============================================================

class AIAssistantView(BaseView):
    def __init__(self,page):
        super().__init__(page,"/ai-assistant")
        self.chat=ft.Column(scroll=ft.ScrollMode.ADAPTIVE,expand=True)
        self.input=ft.TextField(label="Equipos'a sor...",multiline=True,min_lines=1,max_lines=4,on_submit=self.ask)
        
        chips = [
            ("💰 Bugünkü Net Kârımız?", self.q_profit),
            ("🏆 En Çok Satanlar?", self.q_top_products),
            ("✂️ Kesilen Komisyonlar?", self.q_comm),
            ("⚠️ Kritik Stok Uyarıları?", self.q_stock),
            ("🔥 Bugünkü Fire Maliyeti?", self.q_waste),
            ("🛵 Kurye Nakit Beklentisi?", self.q_courier),
            ("👥 Toplam Veresiye Alacak?", self.q_cash_cari)
        ]
        chip_row = ft.Row([ft.ElevatedButton(c[0], bgcolor=ft.colors.with_opacity(0.1, "#8B5CF6"), color="#8B5CF6", on_click=lambda _, f=c[1], q=c[0]: self.trg(q, f)) for c in chips], scroll=ft.ScrollMode.ADAPTIVE)

        self.controls=[
            header(page,"AI ERP Asistanı"),
            ft.Container(content=chip_row, padding=ft.padding.only(bottom=10)),
            ft.Container(content=self.chat, expand=True, border=ft.border.all(1, ft.colors.OUTLINE_VARIANT), border_radius=12, padding=12),
            ft.Row([self.input, ft.IconButton(ft.icons.SEND, on_click=self.ask)], alignment=ft.MainAxisAlignment.END)
        ]
        self.add_ai("Merhaba! Bulut veritabanına bağlandım. Yukarıdaki hazır butonlara tıklayarak veya bana yazarak işletmenin anlık analizini yapabilirsin.")

    def build_context(self):
        with db_cursor() as c:
            c.execute("SELECT COALESCE(SUM(total_amount),0) revenue, COALESCE(SUM(profit_amount),0) profit, COALESCE(SUM(commission_amount),0) comm, COUNT(*) sales FROM sales WHERE SUBSTRING(created_at,1,10)=%s AND status='completed'",(today_str(),));today=c.fetchone()
            c.execute("SELECT name,current_stock,critical_level,unit FROM raw_materials WHERE current_stock<=critical_level ORDER BY current_stock");low=c.fetchall()
            c.execute("SELECT product_name,SUM(quantity) qty,SUM(total_price) revenue FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE SUBSTRING(s.created_at,1,10)=%s AND s.status='completed' GROUP BY product_name ORDER BY qty DESC LIMIT 10",(today_str(),));top=c.fetchall()
            c.execute("SELECT COALESCE(SUM(total_cost),0) waste FROM waste_records WHERE SUBSTRING(created_at,1,10)=%s",(today_str(),));waste=c.fetchone()["waste"]
            c.execute("SELECT COUNT(*) n FROM tables WHERE status='occupied'");occupied=c.fetchone()["n"]
        return {"today":dict(today),"low_stock":[dict(x) for x in low],"top_products":[dict(x) for x in top],"waste_cost":waste,"occupied_tables":occupied}
        
    def add_user(self, t): self.chat.controls.append(ft.Row([ft.Container(bgcolor=ft.colors.with_opacity(0.12, ft.colors.PRIMARY), border_radius=10, padding=10, content=ft.Text(f"👤 {t}"))], alignment=ft.MainAxisAlignment.END)); self.update_page()
    def add_ai(self, t): self.chat.controls.append(ft.Row([ft.Container(bgcolor=ft.colors.with_opacity(0.08, "#8B5CF6"), border_radius=10, padding=10, content=ft.Text(f"🤖 {t}"))], alignment=ft.MainAxisAlignment.START)); self.update_page()
    def trg(self, q, f): self.add_user(q); self.add_ai(f())

    def ask(self,e):
        q=(self.input.value or "").strip()
        if not q:return
        self.input.value=""
        self.add_user(q)
        context=self.build_context()
        answer=self.local_answer(q,context)
        if answer is None:
            answer=self.gemini_answer(q,context)
        self.add_ai(answer)

    def local_answer(self,q,ctx):
        q=q.lower()
        if any(x in q for x in ["bugün", "ciro", "satış"]): return f"Bugünkü ciro {money(ctx['today']['revenue'])}, net kâr {money(ctx['today']['profit'])} ve {ctx['today']['sales']} satış var. Kesilen toplam komisyon: {money(ctx['today']['comm'])}."
        if "komisyon" in q or "kesinti" in q: return self.q_comm()
        if "kritik stok" in q or "stok" in q and "kritik" in q: return self.q_stock()
        if "fire" in q or "zayi" in q: return self.q_waste()
        if "çok sat" in q or "en çok" in q: return self.q_top_products()
        if "veresiye" in q or "alacak" in q: return self.q_cash_cari()
        return None
        
    def q_profit(self):
        with db_cursor() as c:
            c.execute("SELECT SUM(total_amount) as r, SUM(profit_amount) as p FROM sales WHERE SUBSTRING(created_at,1,10) = %s AND status='completed'", (today_str(),))
            st = c.fetchone()
        return f"Bugünkü brüt ciro: {money(st['r'])}\nReçete maliyetleri ve komisyonlar düşüldüğünde elde edilen Net Kâr: {money(st['p'])}"
        
    def q_top_products(self):
        with db_cursor() as c:
            c.execute("SELECT product_name, SUM(quantity) as qty FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE SUBSTRING(s.created_at,1,10)=%s AND s.status='completed' GROUP BY product_name ORDER BY qty DESC LIMIT 5", (today_str(),))
            res = c.fetchall()
        if not res: return "Bugün henüz ürün satışı yapılmamış."
        return "🏆 Bugün en çok satan 5 ürün:\n" + "\n".join([f"• {r['product_name']}: {r['qty']} adet" for r in res])
        
    def q_comm(self):
        with db_cursor() as c:
            c.execute("SELECT COALESCE(SUM(commission_amount), 0) as comm FROM sales WHERE SUBSTRING(created_at,1,10)=%s AND status='completed'", (today_str(),))
            res = c.fetchone()["comm"]
        return f"✂️ Bugün banka POS ve Yemek Platformlarına toplam {money(res)} komisyon ödedik."
        
    def q_stock(self):
        with db_cursor() as c:
            c.execute("SELECT name, current_stock, unit FROM raw_materials WHERE current_stock <= critical_level")
            res = c.fetchall()
        if not res: return "Şu an stok seviyesi kritik olan hiçbir hammadde yok, güvendeyiz!"
        return "⚠️️ Acil tedarik edilmesi gerekenler:\n" + "\n".join([f"• {r['name']}: {r['current_stock']} {r['unit']} kaldı!" for r in res])
        
    def q_waste(self):
        with db_cursor() as c:
            c.execute("SELECT COALESCE(SUM(total_cost), 0) as waste FROM waste_records WHERE SUBSTRING(created_at,1,10)=%s", (today_str(),))
            res = c.fetchone()["waste"]
        return f"🔥 Bugün sisteme girilen zayi/fire ürünlerin işletmeye toplam maliyeti: {money(res)}."
        
    def q_courier(self):
        with db_cursor() as c:
            c.execute("SELECT cr.name, SUM(s.cash_amount) as kn FROM sales s JOIN couriers cr ON s.courier_id = cr.id WHERE SUBSTRING(s.created_at,1,10) = %s AND s.status='completed' GROUP BY cr.name", (today_str(),))
            res = c.fetchall()
        if not res: return "Bugün kuryeler üzerinden kapıda nakit tahsilatı yapılmamış."
        return "🛵 Kurye Nakit Mutabakatı (Cepteki Para):\n" + "\n".join([f"• {r['name']}: {money(r['kn'])}" for r in res])
        
    def q_cash_cari(self):
        with db_cursor() as c:
            c.execute("SELECT COALESCE(SUM(current_balance), 0) as bal FROM customer_accounts WHERE current_balance > 0")
            bal = c.fetchone()["bal"]
        return f"👥 Şu an müşterilerimizde (veresiye) toplam {money(bal)} alacağımız bulunuyor."

    def gemini_answer(self,q,ctx):
        key=os.getenv("GEMINI_API_KEY")
        if not key:return "Bu soru için yerel rapor verisi yeterli değil. GEMINI_API_KEY tanımlarsan metin tabanlı AI analizi de kullanabilirim."
        try:
            import requests
            prompt=("Sen Equipos için Türkçe çalışan bir işletme analiz asistanısın. Sesli özellik kullanma. "
                    "Yalnızca verilen ERP bağlamına dayan, veri yoksa açıkça belirt. Kısa ve uygulanabilir cevap ver.\n"
                    f"ERP BAĞLAMI: {ctx}\nKULLANICI SORUSU: {q}")
            url="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key="+urllib.parse.quote(key)
            r=requests.post(url,json={"contents":[{"parts":[{"text":prompt}]}]},timeout=20)
            if r.ok:
                data=r.json();return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return "AI servisine ulaşılamadı. Yerel raporları kullanabilirsin."
        except Exception as ex:return f"AI bağlantısı kurulamadı: {ex}"

# =============================================================
# ROUTER
# =============================================================

def main(page: ft.Page):
    init_db()
    page.title="Equipos"
    page.theme=ft.Theme(color_scheme_seed="#EC4899")
    page.dark_theme=ft.Theme(color_scheme_seed="#EC4899")
    page.theme_mode=ft.ThemeMode.LIGHT
    page.padding=0
    
    # Masaüstü uygulaması için (safari/web modunda hata vermez)
    page.window.min_width = 900
    page.window.min_height = 650
    
    def route_change(e):
        page.views.clear();r=page.route
        if r=="/splash":page.views.append(SplashView(page))
        elif r=="/login":page.views.append(LoginView(page))
        elif r=="/dashboard":page.views.append(DashboardView(page))
        elif r=="/tables":page.views.append(TablesView(page))
        elif r.startswith("/order/"):
            try:page.views.append(OrderView(page,int(r.split("/")[-1])))
            except:page.go("/tables")
        elif r=="/quick-pos":page.views.append(QuickPOSView(page))
        elif r=="/inventory":page.views.append(InventoryView(page))
        elif r=="/products":page.views.append(ProductsView(page))
        elif r=="/cash":page.views.append(CashView(page))
        elif r=="/shifts":page.views.append(ShiftsView(page))
        elif r=="/suppliers":page.views.append(SuppliersView(page))
        elif r=="/reservations":page.views.append(ReservationsView(page))
        elif r=="/loyalty":page.views.append(LoyaltyView(page))
        elif r=="/campaigns":page.views.append(CampaignsView(page))
        elif r=="/reports":page.views.append(ReportsView(page))
        elif r=="/settings":page.views.append(SettingsView(page))
        elif r=="/ai-assistant":page.views.append(AIAssistantView(page))
        else:page.go("/splash")
        page.update()
    page.on_route_change=route_change
    def realtime_refresh():
        while True:
            time.sleep(10)
            try:
                if page.route == "/dashboard":
                    page.views.clear(); page.views.append(DashboardView(page)); page.update()
            except Exception:
                pass
    threading.Thread(target=realtime_refresh,daemon=True).start()
    page.go("/splash")

if __name__=="__main__":
    port = int(os.environ.get("PORT", 10000))
    ft.app(
        target=main, 
        view=ft.AppView.WEB_BROWSER, 
        host="0.0.0.0", 
        port=port,
        assets_dir="assets"
    )
