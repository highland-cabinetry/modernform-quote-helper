import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

const orderPalette = ["#2f6f73", "#315b7d", "#d09b3c", "#9b6a3c", "#8b6f9c", "#c7764a"];
const cameraStates = new Map();

function sourceColor(source) {
  let hash = 0;
  for (let i = 0; i < source.length; i += 1) hash = (hash * 31 + source.charCodeAt(i)) | 0;
  return orderPalette[Math.abs(hash) % orderPalette.length];
}

function itemColor(item, colorMode) {
  if (colorMode === "fragility") return item.fragile ? "#d9564b" : "#4f7f72";
  if (colorMode === "order") return sourceColor(item.source_refs?.[0] || "Manual");
  return item.color || "#315b7d";
}

export default function PalletViewer({ pallet, colorMode, onSelect, selectedId }) {
  const hostRef = useRef(null);
  const runtimeRef = useRef(null);
  const onSelectRef = useRef(onSelect);
  const [resetVersion, setResetVersion] = useState(0);

  useEffect(() => { onSelectRef.current = onSelect; }, [onSelect]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !pallet) return undefined;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#eef2f1");
    scene.fog = new THREE.Fog("#eef2f1", 160, 300);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 1000);
    const span = Math.max(pallet.length, pallet.width, pallet.max_total_height);
    const defaultPosition = new THREE.Vector3(span * 0.95, span * 0.72, span * 0.95);
    const defaultTarget = new THREE.Vector3(0, Math.max(12, pallet.stack_height * 0.38), 0);
    const saved = cameraStates.get(pallet.pallet_number);
    camera.position.copy(saved?.position || defaultPosition);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    controls.dampingFactor = 0.07;
    controls.target.copy(saved?.target || defaultTarget);
    controls.minDistance = 35;
    controls.maxDistance = 360;
    let animationId = null;
    const render = () => { animationId = null; controls.update(); renderer.render(scene, camera); };
    const invalidate = () => { if (animationId === null) animationId = requestAnimationFrame(render); };
    controls.addEventListener("change", () => {
      invalidate();
      cameraStates.set(pallet.pallet_number, { position: camera.position.clone(), target: controls.target.clone() });
    });

    scene.add(new THREE.HemisphereLight("#ffffff", "#73808a", 2.1));
    const keyLight = new THREE.DirectionalLight("#fff6e8", 2.8);
    keyLight.position.set(70, 110, 55);
    keyLight.castShadow = true;
    keyLight.shadow.mapSize.set(2048, 2048);
    scene.add(keyLight);

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(320, 320), new THREE.MeshStandardMaterial({ color: "#e2e8e6", roughness: 0.95 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -pallet.deck_height - 0.25;
    floor.receiveShadow = true;
    scene.add(floor);
    const grid = new THREE.GridHelper(200, 40, "#c3cdca", "#d6dedb");
    grid.position.y = -pallet.deck_height + 0.05;
    scene.add(grid);

    const deck = new THREE.Mesh(new THREE.BoxGeometry(pallet.length, pallet.deck_height, pallet.width), new THREE.MeshStandardMaterial({ color: "#9b7448", roughness: 0.82 }));
    deck.position.set(0, -pallet.deck_height / 2, 0);
    deck.castShadow = true;
    deck.receiveShadow = true;
    scene.add(deck);
    const deckEdges = new THREE.LineSegments(new THREE.EdgesGeometry(deck.geometry), new THREE.LineBasicMaterial({ color: "#4c3928", transparent: true, opacity: 0.7 }));
    deckEdges.position.copy(deck.position);
    scene.add(deckEdges);

    const maxBoundary = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(pallet.length, pallet.max_cargo_height, pallet.width)),
      new THREE.LineDashedMaterial({ color: pallet.freight_height > pallet.max_total_height ? "#d9564b" : "#8c9ba0", dashSize: 2, gapSize: 1.4, opacity: 0.7, transparent: true }),
    );
    maxBoundary.position.set(0, pallet.max_cargo_height / 2, 0);
    maxBoundary.computeLineDistances();
    scene.add(maxBoundary);

    const pickable = [];
    const visuals = new Map();
    for (const item of pallet.items) {
      const geometry = new THREE.BoxGeometry(item.length, item.height, item.width);
      const material = new THREE.MeshStandardMaterial({ color: itemColor(item, colorMode), roughness: 0.58, metalness: 0.03, transparent: true, opacity: 0.93 });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(item.x + item.length / 2 - pallet.length / 2, item.z + item.height / 2, item.y + item.width / 2 - pallet.width / 2);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.item = item;
      scene.add(mesh);
      pickable.push(mesh);
      const outlineMaterial = new THREE.LineBasicMaterial({ color: item.bundle_id ? "#7f5bb3" : "#15354b", opacity: 0.55, transparent: true });
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), outlineMaterial);
      outline.position.copy(mesh.position);
      scene.add(outline);
      visuals.set(item.unit_id, { item, material, outlineMaterial });
    }

    for (const board of pallet.load_boards || []) {
      const geometry = new THREE.BoxGeometry(board.length, board.height, board.width);
      const material = new THREE.MeshStandardMaterial({ color: "#c8a97e", roughness: 0.85, transparent: true, opacity: 0.85 });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(board.x + board.length / 2 - pallet.length / 2, board.z + board.height / 2, board.y + board.width / 2 - pallet.width / 2);
      mesh.receiveShadow = true;
      scene.add(mesh);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineBasicMaterial({ color: "#7a5a33", opacity: 0.7, transparent: true }));
      edges.position.copy(mesh.position);
      scene.add(edges);
    }

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const handlePointer = (event) => {
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(pickable, false)[0];
      if (hit) onSelectRef.current?.(hit.object.userData.item);
    };
    renderer.domElement.addEventListener("pointerup", handlePointer);
    const resize = () => {
      const width = host.clientWidth || 800;
      const height = host.clientHeight || 520;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      invalidate();
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(host);
    resize();

    runtimeRef.current = { camera, controls, defaultPosition, defaultTarget, visuals, invalidate };
    invalidate();
    return () => {
      cancelAnimationFrame(animationId);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("pointerup", handlePointer);
      controls.dispose();
      scene.traverse((object) => { object.geometry?.dispose?.(); Array.isArray(object.material) ? object.material.forEach((entry) => entry.dispose()) : object.material?.dispose?.(); });
      renderer.dispose();
      renderer.domElement.remove();
      runtimeRef.current = null;
    };
  }, [pallet, resetVersion]);

  useEffect(() => {
    for (const [unitId, visual] of runtimeRef.current?.visuals || []) {
      const selected = unitId === selectedId;
      visual.material.color.set(itemColor(visual.item, colorMode));
      visual.material.opacity = selected ? 1 : 0.93;
      visual.material.emissive.set(selected ? "#f3b63f" : "#000000");
      visual.material.emissiveIntensity = selected ? 0.3 : 0;
      visual.outlineMaterial.color.set(selected ? "#f6c758" : visual.item.bundle_id ? "#7f5bb3" : "#15354b");
      visual.outlineMaterial.opacity = selected ? 1 : 0.55;
    }
    runtimeRef.current?.invalidate();
  }, [colorMode, selectedId, pallet, resetVersion]);

  const resetView = () => {
    if (!pallet) return;
    cameraStates.delete(pallet.pallet_number);
    setResetVersion((value) => value + 1);
  };

  if (!pallet) return <div className="viewer-empty">Run a pallet plan to open the 3D build view.</div>;
  return <div className="viewer-host"><div ref={hostRef} className="pallet-viewer" aria-label={`3D visualization of pallet ${pallet.pallet_number}`} /><button className="btn btn-secondary btn-sm viewer-reset" type="button" onClick={resetView}>Reset view</button></div>;
}
