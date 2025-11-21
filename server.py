# server.py
import socket
import threading
import time
import math
import argparse
from common import send_msg, recv_msg
from collections import deque

# Game constants
TICK_RATE = 20  # updates per second
SNAPSHOT_RATE = 10  # snapshots per second sent to clients
PORT_DEFAULT = 9009

# Game entities unique id generator
def gen_id():
    i = 1
    while True:
        yield i
        i += 1

ID_GEN = gen_id()

# Waypoints for enemy path (fixed)
WAYPOINTS = [(50, 300), (200, 300), (200, 100), (600, 100), (600, 400), (800, 400)]

# Entity classes (simple dict-based)
class GameState:
    def __init__(self):
        self.lock = threading.RLock()
        self.enemies = {}  # id -> dict
        self.towers = {}   # id -> dict
        self.bullets = {}  # id -> dict
        self.players = {}  # sock -> player dict {id, name, money, lives}
        self.next_enemy_spawn = time.time() + 1.0
        self.enemy_spawn_interval = 2.0
        self.wave = 1
        self.target_lives = 20
        self.map_size = (900, 600)

    def snapshot(self):
        with self.lock:
            # produce a JSON-serializable snapshot
            return {
                "enemies": list(self.enemies.values()),
                "towers": list(self.towers.values()),
                "bullets": list(self.bullets.values()),
                "players": list(self.players.values()),
                "wave": self.wave,
                "time": time.time()
            }

state = GameState()

# Game logic functions
def spawn_enemy():
    eid = next(ID_GEN)
    enemy = {
        "id": eid,
        "hp": 10 + state.wave * 2,
        "pos": list(WAYPOINTS[0]),
        "speed": 40 + state.wave * 2,  # pixels per second
        "wp_index": 1
    }
    state.enemies[eid] = enemy

def update_enemies(dt):
    remove = []
    for eid, e in list(state.enemies.items()):
        if e["wp_index"] >= len(WAYPOINTS):
            # reached end -> damage players (distribute damage across players)
            total_players = len(state.players)
            if total_players == 0:
                # no players: just remove
                remove.append(eid)
            else:
                dmg_per_player = 1
                for p in state.players.values():
                    p["lives"] = max(0, p["lives"] - dmg_per_player)
                remove.append(eid)
            continue
        target = WAYPOINTS[e["wp_index"]]
        dx = target[0] - e["pos"][0]
        dy = target[1] - e["pos"][1]
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            e["wp_index"] += 1
        else:
            step = e["speed"] * dt
            e["pos"][0] += dx/dist * step
            e["pos"][1] += dy/dist * step
    for rid in remove:
        del state.enemies[rid]

def towers_fire(dt):
    # For each tower, find nearest enemy in range -> create bullet
    for tid, t in list(state.towers.items()):
        t.setdefault("cooldown", 0.0)
        t["cooldown"] -= dt
        if t["cooldown"] > 0:
            continue
        # find nearest enemy
        target = None
        best_d = 1e9
        for e in state.enemies.values():
            d = math.hypot(e["pos"][0]-t["pos"][0], e["pos"][1]-t["pos"][1])
            if d <= t["range"] and d < best_d:
                best_d = d
                target = e
        if target:
            # fire
            bid = next(ID_GEN)
            bullet = {
                "id": bid,
                "pos": list(t["pos"]),
                "target_id": target["id"],
                "speed": 300,
                "damage": t["damage"]
            }
            state.bullets[bid] = bullet
            t["cooldown"] = 1.0 / t["fire_rate"]

def update_bullets(dt):
    remove = []
    for bid, b in list(state.bullets.items()):
        # target may be gone
        target = state.enemies.get(b["target_id"])
        if not target:
            remove.append(bid)
            continue
        dx = target["pos"][0] - b["pos"][0]
        dy = target["pos"][1] - b["pos"][1]
        dist = math.hypot(dx, dy)
        if dist < 5.0:
            # hit
            target["hp"] -= b["damage"]
            remove.append(bid)
            if target["hp"] <= 0:
                # reward a player (round-robin)
                # choose first player to reward
                players = list(state.players.values())
                if players:
                    players[0]["money"] += 5
                if target["id"] in state.enemies:
                    del state.enemies[target["id"]]
            continue
        step = b["speed"] * dt
        b["pos"][0] += dx/dist * step
        b["pos"][1] += dy/dist * step
    for rid in remove:
        if rid in state.bullets:
            del state.bullets[rid]

def game_update_loop():
    last = time.time()
    snapshot_interval = 1.0 / SNAPSHOT_RATE
    last_snapshot = time.time()
    while True:
        now = time.time()
        dt = now - last
        if dt <= 0:
            time.sleep(0.001)
            continue
        last = now
        with state.lock:
            # spawn enemies periodically
            if now >= state.next_enemy_spawn:
                spawn_enemy()
                state.next_enemy_spawn = now + state.enemy_spawn_interval
            update_enemies(dt)
            towers_fire(dt)
            update_bullets(dt)
            # wave progression: simple rule, if no enemies for a while, next wave
            if len(state.enemies) == 0 and now - state.next_enemy_spawn > 1.0:
                state.wave += 1
                state.enemy_spawn_interval = max(0.5, 2.0 - state.wave * 0.05)
        # send periodic snapshots via broadcaster
        if now - last_snapshot >= snapshot_interval:
            broadcast_snapshot()
            last_snapshot = now
        time.sleep(max(0, 1.0 / TICK_RATE - (time.time() - now)))

# networking
clients = {}  # sock -> (thread, addr)

clients_lock = threading.RLock()

def broadcast_snapshot():
    snap = {"type": "snapshot", "state": state.snapshot()}
    with clients_lock:
        for sock in list(clients.keys()):
            try:
                send_msg(sock, snap)
            except Exception:
                # client probably disconnected
                remove_client(sock)

def handle_client(sock, addr):
    print("Client handler started for", addr)
    player_id = next(ID_GEN)
    try:
        # register basic player with default money/lives
        with state.lock:
            state.players[player_id] = {
                "id": player_id,
                "name": f"Player{player_id}",
                "money": 100,
                "lives": state.target_lives
            }
        # send welcome
        send_msg(sock, {"type": "welcome", "player_id": player_id})
        while True:
            msg = recv_msg(sock)
            if msg is None:
                break
            mtype = msg.get("type")
            if mtype == "join":
                with state.lock:
                    state.players[player_id]["name"] = msg.get("name", state.players[player_id]["name"])
            elif mtype == "place_tower":
                x = msg.get("x"); y = msg.get("y"); ttype = msg.get("tower_type", "basic")
                with state.lock:
                    player = state.players.get(player_id)
                    if player and player["money"] >= 20:
                        tid = next(ID_GEN)
                        tower = {
                            "id": tid,
                            "owner": player_id,
                            "pos": [x, y],
                            "type": ttype,
                            # basic stats
                            "range": 120 if ttype == "basic" else 160,
                            "damage": 4 if ttype == "basic" else 8,
                            "fire_rate": 1.0 if ttype == "basic" else 0.6
                        }
                        state.towers[tid] = tower
                        player["money"] -= 20
                        # ack
                        send_msg(sock, {"type": "ok", "msg": "tower placed", "tower_id": tid})
                    else:
                        send_msg(sock, {"type": "error", "msg": "not enough money or player missing"})
            else:
                send_msg(sock, {"type": "error", "msg": "unknown message type"})
    except Exception as e:
        print("Exception in client handler:", e)
    finally:
        print("Client disconnected:", addr)
        remove_client(sock)
        # free player's resources
        with state.lock:
            if player_id in state.players:
                del state.players[player_id]
        sock.close()

def remove_client(sock):
    with clients_lock:
        if sock in clients:
            th, addr = clients[sock]
            try:
                sock.close()
            except:
                pass
            del clients[sock]

def accept_loop(listen_sock):
    while True:
        sock, addr = listen_sock.accept()
        print("Accepted connection from", addr)
        with clients_lock:
            th = threading.Thread(target=handle_client, args=(sock, addr), daemon=True)
            clients[sock] = (th, addr)
            th.start()

def main(host='0.0.0.0', port=PORT_DEFAULT):
    print("Server starting on", host, port)
    listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen.bind((host, port))
    listen.listen(8)
    ac = threading.Thread(target=accept_loop, args=(listen,), daemon=True)
    ac.start()
    gl = threading.Thread(target=game_update_loop, daemon=True)
    gl.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down server.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    args = parser.parse_args()
    main(args.host, args.port)
