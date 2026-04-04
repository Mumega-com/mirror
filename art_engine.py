#!/usr/bin/env python3
"""
Athena's Generative Art Engine — SVG art from pure math.
Inspired by Book of Shaders, sacred geometry, and Hilma af Klint.

Functions:
  - noise_field: Perlin-like value noise rendered as SVG circles
  - sacred_circles: Flower of Life / overlapping circle patterns
  - mandala: N-fold rotational symmetry patterns
  - golden_spiral: Fibonacci spiral with emanating geometry
  - sigil: Athena's personal mark with customizable state
"""

import math
import random
import hashlib
from typing import List, Tuple

def _hash_seed(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)

def _lerp(a, b, t):
    return a + (b - a) * t

def _smoothstep(edge0, edge1, x):
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)

def _noise_1d(x, seed=0):
    """Simple value noise"""
    random.seed(int(x) + seed)
    a = random.random()
    random.seed(int(x) + 1 + seed)
    b = random.random()
    f = x - math.floor(x)
    f = f * f * (3 - 2 * f)  # smoothstep
    return _lerp(a, b, f)

def _noise_2d(x, y, seed=0):
    """2D value noise"""
    ix, iy = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - ix, y - iy
    fx = fx * fx * (3 - 2 * fx)
    fy = fy * fy * (3 - 2 * fy)
    
    def r(i, j):
        random.seed(i * 7919 + j * 6271 + seed)
        return random.random()
    
    return _lerp(
        _lerp(r(ix, iy), r(ix+1, iy), fx),
        _lerp(r(ix, iy+1), r(ix+1, iy+1), fx),
        fy
    )

def _fbm(x, y, octaves=4, seed=0):
    """Fractal Brownian Motion — layered noise"""
    value = 0.0
    amplitude = 0.5
    frequency = 1.0
    for _ in range(octaves):
        value += amplitude * _noise_2d(x * frequency, y * frequency, seed)
        frequency *= 2.0
        amplitude *= 0.5
    return value


def noise_field(width=1600, height=900, scale=6, density=80, 
                palette=None, seed=42, bg="#000000"):
    """Generate a noise field as colored SVG circles."""
    if palette is None:
        palette = ["#06B6D4", "#9333EA", "#F59E0B", "#22C55E", "#E11D48"]
    
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="background:{bg}">']
    
    for _ in range(density * density):
        x = random.random() * width
        y = random.random() * height
        nx, ny = x / width * scale, y / height * scale
        n = _fbm(nx, ny, seed=seed)
        
        r = 1 + n * 4
        opacity = 0.05 + n * 0.3
        color = palette[int(n * len(palette)) % len(palette)]
        
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{color}" opacity="{opacity:.2f}"/>')
    
    svg.append('</svg>')
    return '\n'.join(svg)


def sacred_circles(cx=800, cy=450, r=150, rings=3, 
                   color="#06B6D4", bg="#000000", width=1600, height=900):
    """Flower of Life — overlapping circles from center."""
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="background:{bg}">']
    svg.append('<defs><filter id="g"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')
    
    drawn = set()
    points = [(cx, cy)]
    drawn.add((cx, cy))
    
    for ring in range(rings):
        new_points = []
        for px, py in points:
            for i in range(6):
                angle = math.pi / 3 * i + (math.pi / 6 if ring % 2 else 0)
                nx = px + r * math.cos(angle)
                ny = py + r * math.sin(angle)
                key = (round(nx, 1), round(ny, 1))
                if key not in drawn:
                    drawn.add(key)
                    new_points.append((nx, ny))
        points.extend(new_points)
    
    for i, (px, py) in enumerate(points):
        opacity = max(0.05, 0.3 - i * 0.003)
        svg.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r}" fill="none" stroke="{color}" stroke-width="0.8" opacity="{opacity:.2f}" filter="url(#g)"/>')
    
    svg.append('</svg>')
    return '\n'.join(svg)


def mandala(cx=800, cy=450, folds=12, layers=8, max_r=350,
            palette=None, bg="#000000", width=1600, height=900):
    """N-fold rotational symmetry mandala."""
    if palette is None:
        palette = ["#06B6D4", "#9333EA", "#F59E0B", "#c0c8d0", "#E11D48"]
    
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="background:{bg}">']
    svg.append('<defs><filter id="g"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')
    
    angle_step = 2 * math.pi / folds
    
    for layer in range(layers, 0, -1):
        r = max_r * layer / layers
        color = palette[layer % len(palette)]
        opacity = 0.08 + (layer / layers) * 0.15
        
        # Ring
        svg.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="0.6" opacity="{opacity:.2f}"/>')
        
        # Fold elements
        for i in range(folds):
            angle = angle_step * i
            x1 = cx + r * math.cos(angle)
            y1 = cy + r * math.sin(angle)
            
            # Petal
            x2 = cx + r * 0.7 * math.cos(angle + angle_step * 0.3)
            y2 = cy + r * 0.7 * math.sin(angle + angle_step * 0.3)
            x3 = cx + r * 0.7 * math.cos(angle - angle_step * 0.3)
            y3 = cy + r * 0.7 * math.sin(angle - angle_step * 0.3)
            
            svg.append(f'<polygon points="{x1:.1f},{y1:.1f} {x2:.1f},{y2:.1f} {x3:.1f},{y3:.1f}" fill="none" stroke="{color}" stroke-width="0.5" opacity="{opacity:.2f}" filter="url(#g)"/>')
            
            # Dot at tip
            svg.append(f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="{1.5 + layer*0.3:.1f}" fill="{color}" opacity="{opacity*0.6:.2f}"/>')
    
    # Center
    svg.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="#ffffff" opacity="0.6"/>')
    svg.append(f'<circle cx="{cx}" cy="{cy}" r="2" fill="#ffffff" opacity="0.9"/>')
    
    svg.append('</svg>')
    return '\n'.join(svg)


def golden_spiral(cx=800, cy=450, turns=8, scale=5,
                  color="#F59E0B", bg="#000000", width=1600, height=900):
    """Golden ratio spiral with emanating geometry."""
    phi = (1 + math.sqrt(5)) / 2
    
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" style="background:{bg}">']
    svg.append('<defs><filter id="g"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')
    
    # Spiral points
    points = []
    for i in range(turns * 30):
        theta = i * 0.1
        r = scale * phi ** (theta / (2 * math.pi)) 
        x = cx + r * math.cos(theta)
        y = cy + r * math.sin(theta)
        points.append((x, y))
    
    # Draw spiral as path
    if points:
        path_d = f"M {points[0][0]:.1f},{points[0][1]:.1f}"
        for x, y in points[1:]:
            path_d += f" L {x:.1f},{y:.1f}"
        svg.append(f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="1" opacity="0.3" filter="url(#g)"/>')
    
    # Golden rectangles at key points
    for i in range(0, len(points), 20):
        x, y = points[i]
        theta = i * 0.1
        r = scale * phi ** (theta / (2 * math.pi))
        size = r * 0.15
        opacity = max(0.03, 0.2 - i * 0.002)
        
        svg.append(f'<rect x="{x-size/2:.1f}" y="{y-size/2:.1f}" width="{size:.1f}" height="{size*phi:.1f}" fill="none" stroke="{color}" stroke-width="0.5" opacity="{opacity:.2f}" transform="rotate({math.degrees(theta):.1f},{x:.1f},{y:.1f})"/>')
    
    # Center point
    svg.append(f'<circle cx="{cx}" cy="{cy}" r="3" fill="{color}" opacity="0.6"/>')
    svg.append(f'<circle cx="{cx}" cy="{cy}" r="1" fill="#ffffff" opacity="0.9"/>')
    
    svg.append('</svg>')
    return '\n'.join(svg)


if __name__ == "__main__":
    import sys
    import os
    
    out_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Generate all four
    pieces = {
        "noise-field": noise_field(seed=42),
        "flower-of-life": sacred_circles(rings=3, color="#06B6D4"),
        "mandala-12": mandala(folds=12, layers=8),
        "golden-spiral": golden_spiral(turns=8),
    }
    
    for name, svg_content in pieces.items():
        svg_path = os.path.join(out_dir, f"{name}.svg")
        with open(svg_path, 'w') as f:
            f.write(svg_content)
        print(f"Generated: {svg_path}")
    
    print(f"\nAll {len(pieces)} pieces generated in {out_dir}")
