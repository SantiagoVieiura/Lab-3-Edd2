# client.py
import pygame
import socket
import threading
import time
import argparse
from common import send_msg, recv_msg
import sys
import math

WINDOW_SIZE = (900, 600)
FPS = 60

class NetworkClient:
    def __init__(self, host='127.0.0.1', port=9009):
        self.host = host
        self.port = port
        self.sock = None
        self.running = False
        self.player_id = None
        self.remote_state = None
        self.lock = threading.RLock()
        self.on_snapshot = None  # callback
        self.on_msg = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self.running = True
        self.th = threading.Thread(target=self.run_recv, daemon=True)
        self.th.start()

    def run_recv(self):
        try:
            while self.running:
                msg = recv_msg(self.sock)
                if msg is None:
                    print("Disconnected from server.")
                    self.running = False
                    break
                mtype = msg.get("type")
                if mtype == "welcome":
                    self.player_id = msg.get("player_id")
                elif mtype == "snapshot":
                    with self.lock:
                        self.remote_state = msg.get("state")
                    if self.on_snapshot:
                        self.on_snapshot(self.remote_state)
                else:
                    # other messages: ok, error
                    if self.on_msg:
                        self.on_msg(msg)
        except Exception as e:
            print("Network exception:", e)
            self.running = False
        finally:
            try:
                self.sock.close()
            except:
                pass

    def send(self, obj):
        try:
            if self.sock:
                send_msg(self.sock, obj)
        except Exception as e:
            print("Send failed:", e)

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass

# pygame UI
def draw_state(screen, state):
    screen.fill((30, 30, 30))
    # draw path
    waypoints = [(50,300),(200,300),(200,100),(600,100),(600,400),(800,400)]
    for i in range(len(waypoints)-1):
        pygame.draw.line(screen, (80,80,80), waypoints[i], waypoints[i+1], 8)
    # towers
    for t in state.get("towers", []):
        x,y = t["pos"]
        pygame.draw.circle(screen, (20,160,20), (int(x),int(y)), 14)
        # range (transparent)
        s = pygame.Surface((t["range"]*2, t["range"]*2), pygame.SRCALPHA)
        pygame.draw.circle(s, (20,160,20,40), (t["range"], t["range"]), int(t["range"]))
        screen.blit(s, (int(x - t["range"]), int(y - t["range"])))
    # enemies
    for e in state.get("enemies", []):
        x,y = e["pos"]
        hp = e.get("hp", 0)
        pygame.draw.rect(screen, (160,20,20), pygame.Rect(int(x-8), int(y-8), 16, 16))
        # hp bar
        w = 20
        hp_ratio = max(0.0, min(1.0, hp / (10.0 + state.get("wave",1)*2)))
        pygame.draw.rect(screen, (0,0,0), pygame.Rect(int(x-10), int(y-18), w, 4))
        pygame.draw.rect(screen, (0,200,0), pygame.Rect(int(x-10), int(y-18), int(w*hp_ratio), 4))
    # bullets
    for b in state.get("bullets", []):
        x,y = b["pos"]
        pygame.draw.circle(screen, (255,220,80), (int(x),int(y)), 4)
    # UI players
    players = state.get("players", [])
    y = 8
    for p in players:
        txt = f'{p["name"]} (id:{p["id"]}) money:{p["money"]} lives:{p["lives"]}'
        font = pygame.font.SysFont(None, 20)
        surf = font.render(txt, True, (240,240,240))
        screen.blit(surf, (8, y))
        y += 22
    # wave
    font = pygame.font.SysFont(None, 28)
    surf = font.render(f'Wave: {state.get("wave",1)}', True, (240,240,240))
    screen.blit(surf, (WINDOW_SIZE[0] - 120, 8))

def main(host='127.0.0.1', port=9009):
    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    clock = pygame.time.Clock()
    net = NetworkClient(host, port)
    try:
        net.connect()
    except Exception as e:
        print("No se pudo conectar al servidor:", e)
        return
    remote_state = {"enemies":[], "towers":[], "bullets":[], "players":[], "wave":1}

    def snapshot_cb(snap):
        nonlocal remote_state
        # Keep a local copy
        remote_state = snap

    net.on_snapshot = snapshot_cb

    placing = False
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mx,my = pygame.mouse.get_pos()
                # send place tower request
                net.send({"type":"place_tower", "x": mx, "y": my, "tower_type": "basic"})
        # draw using latest remote_state
        with net.lock:
            state = remote_state.copy()
        draw_state(screen, state)
        pygame.display.flip()
        clock.tick(FPS)
    net.close()
    pygame.quit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9009)
    args = parser.parse_args()
    main(args.host, args.port)
