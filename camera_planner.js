/* ==========================================================================
   TATIP AI — Intelligent Camera Placement & Recommendation Engine
   Modular Frontend Engine (Calculation, 2D Floor Plan, 3D Isometric, Blind Spots, Persistence)
   ========================================================================== */

(function () {
    'use strict';

    const PLANNER_API = typeof API_URL !== 'undefined'
        ? API_URL
        : 'https://tatip-backend-417035159214.us-central1.run.app';

    // ----------------------------------------------------------------------
    // 1. Domain Knowledge Presets (TATIP Camera Types & Business Templates)
    // ----------------------------------------------------------------------
    const CAMERA_PRESETS = {
        wide_4mp: {
            label: 'มุมกว้าง 2.8mm (4MP Wide)',
            resolution: '4MP (2688×1520)',
            lens_label: '2.8mm · FOV 102°',
            fov_deg: 102,
            range_m: 10,
            detection_distance: 'ประมาณ 6–10 ม. (ภาพรวม/Heatmap)',
            default_analytics: ['Heatmap ความหนาแน่น', 'ภาพรวมพื้นที่']
        },
        standard_4mp: {
            label: 'มาตรฐาน 3.6mm (4MP Standard)',
            resolution: '4MP (2688×1520)',
            lens_label: '3.6mm · FOV 88°',
            fov_deg: 88,
            range_m: 12,
            detection_distance: 'ประมาณ 8–12 ม. (ตามหลัก DORI)',
            default_analytics: ['ตรวจจับกิจกรรมบริการ', 'ตรวจสอบขั้นตอนงาน']
        },
        focused_4mp: {
            label: 'เจาะจงระยะ 4mm (4MP Focused)',
            resolution: '4MP (2688×1520)',
            lens_label: '4.0mm · FOV 78°',
            fov_deg: 78,
            range_m: 15,
            detection_distance: 'ประมาณ 10–15 ม. (ประตู/เคาน์เตอร์)',
            default_analytics: ['นับคนเข้า-ออก', 'ระบุรายละเอียดจุดสำคัญ']
        },
        audio_cam: {
            label: 'กล้องวิเคราะห์เสียง TATIP (จำกัด 1 ตัว/สาขา)',
            resolution: '4MP (2688×1520) + Audio Mic',
            lens_label: '3.6mm · FOV 90°',
            fov_deg: 90,
            range_m: 8,
            detection_distance: 'ประมาณ 5–8 ม. (ระยะรับเสียงหน้างานต้องตรวจสอบ)',
            default_analytics: ['วิเคราะห์เสียงสนทนาบริการ', 'กิจกรรมจุดบริการ']
        },
        custom_pending: {
            label: 'กำหนดเอง (รอตรวจสอบสเปกหน้างาน)',
            resolution: 'Pending Verification',
            lens_label: 'Pending Verification (จำลอง FOV 90°)',
            fov_deg: 90,
            range_m: 10,
            detection_distance: 'Pending Verification',
            default_analytics: ['Pending Verification']
        }
    };

    const BUSINESS_TEMPLATES = {
        restaurant: {
            label: 'ร้านอาหาร (Restaurant)',
            defaultWidth: 10,
            defaultLength: 14,
            zones: {
                entrance: { name: 'ทางเข้า-ออก (Entrance)', analytics: ['นับคนเข้า-ออก'], note: 'ระวังแสงย้อนจากประตูกระจก แนะนำเปิด WDR และทดสอบภาพช่วงกลางวัน' },
                cashier: { name: 'เคาน์เตอร์ชำระเงิน (Cashier)', analytics: ['ตรวจจับกิจกรรมจุดชำระเงิน'], note: 'ติดตั้งมุมสูงเอียงเข้าหน้าเคาน์เตอร์ เห็นมือและลิ้นชักเก็บเงินชัดเจน' },
                service: { name: 'พื้นที่บริการ (Service Area)', analytics: ['ตรวจสอบขั้นตอนบริการ'], note: 'ครอบคลุมจุดรับ-ส่งอาหารและจุดติดต่อลูกค้า' },
                seating: { name: 'พื้นที่นั่งลูกค้า (Dining Area)', analytics: ['Heatmap การใช้พื้นที่', 'นับจำนวนลูกค้า'], note: 'หลีกเลี่ยงจุดบอดจากเสาหรือโคมไฟแขวนเพดาน' },
                kitchen: { name: 'พื้นที่ครัว/หลังร้าน (Kitchen)', analytics: ['ตรวจสอบกระบวนการทำงาน'], note: 'หลีกเลี่ยงไอน้ำมัน/ความร้อนโดยตรง ตรวจตำแหน่งฝ้าและปล่องดูดควันหน้างาน' }
            }
        },
        cafe: {
            label: 'ร้านกาแฟ / คาเฟ่ (Café)',
            defaultWidth: 8,
            defaultLength: 10,
            zones: {
                entrance: { name: 'ทางเข้า-ออก (Entrance)', analytics: ['นับคนเข้า-ออก'], note: 'ตรวจจับปริมาณลูกค้าเข้าร้าน ระวังแสงย้อนหน้าร้านกระจก' },
                cashier: { name: 'เคาน์เตอร์สั่งซื้อ (Cashier / POS)', analytics: ['กิจกรรมจุดชำระเงิน'], note: 'ตั้งมุมกล้องครอบคลุมจุดรับออเดอร์และชำระเงิน' },
                service: { name: 'บาร์ชงเครื่องดื่ม (Service Bar)', analytics: ['ขั้นตอนบริการ', 'วิเคราะห์เสียงสนทนาบริการ'], note: 'หากใช้กล้องเสียง (1 ตัว/สาขา) ควรติดใกล้บาร์รับออเดอร์' },
                seating: { name: 'โซนที่นั่งลูกค้า (Seating Area)', analytics: ['Heatmap ความหนาแน่น'], note: 'ดูการกระจายตัวและระยะเวลาการใช้งานโต๊ะ' },
                kitchen: { name: 'พื้นที่เตรียมวัตถุดิบ/เก็บของ', analytics: ['ความปลอดภัยพื้นที่หลังร้าน'], note: 'ตรวจสอบช่องทางเดินและจุดเก็บสต็อก' }
            }
        },
        retail: {
            label: 'ร้านค้าปลีก (Retail Store)',
            defaultWidth: 10,
            defaultLength: 16,
            zones: {
                entrance: { name: 'ทางเข้า-ออกร้าน (Entrance)', analytics: ['นับคนเข้า-ออก'], note: 'เน้นความคมชัดช่วงเดินผ่านประตู (DORI Identify/Recognize)' },
                cashier: { name: 'จุดชำระเงิน (Cashier)', analytics: ['กิจกรรมจุดชำระเงิน'], note: 'ตรวจจับความเคลื่อนไหวหน้าแคชเชียร์' },
                service: { name: 'จุดบริการลูกค้า / แพ็กสินค้า', analytics: ['ขั้นตอนบริการ'], note: 'ครอบคลุมเคาน์เตอร์บริการและจุดรับสินค้า' },
                seating: { name: 'แนวชั้นวางสินค้า (Display Aisles)', analytics: ['Heatmap ความสนใจสินค้า', 'พฤติกรรมเดินชม'], note: 'จัดตำแหน่งตามแนวทางเดินชั้นวาง เพื่อลดจุดอับจากชั้นสูง' },
                kitchen: { name: 'ห้องสต็อกสินค้า (Stock Room)', analytics: ['ตรวจนับการเบิกจ่ายสต็อก'], note: 'ติดหันเข้าทางเดินสต็อกและประตูหลังร้าน' }
            }
        },
        warehouse: {
            label: 'คลังสินค้า / โรงงาน (Warehouse)',
            defaultWidth: 16,
            defaultLength: 24,
            zones: {
                entrance: { name: 'ประตูรับ-ส่งสินค้า (Loading Gate)', analytics: ['ตรวจสอบการเข้า-ออกยานพาหนะ/คน'], note: 'ติดระดับสูงครอบคลุมลานโหลดและประตูม้วน' },
                cashier: { name: 'จุดตรวจเอกสาร/Dispatch', analytics: ['กิจกรรมจุดส่งมอบ'], note: 'ตรวจสอบการเซ็นรับ-ส่งเอกสารและพัสดุ' },
                service: { name: 'พื้นที่คัดแยกสินค้า (Sorting Area)', analytics: ['ขั้นตอนปฏิบัติงาน'], note: 'ครอบคลุมสายพานหรือจุดจัดเรียงสินค้า' },
                seating: { name: 'โถงทางเดินชั้นวางหลัก (Main Aisles)', analytics: ['Heatmap เส้นทางเคลื่อนย้าย'], note: 'แนวชั้นวางสูงต้องตรวจหน้างานเพื่อป้องกันจุดอับระหว่าง Rack' },
                kitchen: { name: 'จุดเก็บสินค้ามูลค่าสูง / สำนักงานคลัง', analytics: ['ความปลอดภัยพื้นที่เฉพาะ'], note: 'ตรวจสอบแสงสว่างและความสูงเพดานจริงหน้างาน' }
            }
        },
        office: {
            label: 'สำนักงาน / อาคาร (Office)',
            defaultWidth: 12,
            defaultLength: 14,
            zones: {
                entrance: { name: 'ประตูเข้า-ออกหลัก (Main Entrance)', analytics: ['นับคนเข้า-ออก'], note: 'ติดตั้งบริเวณโถงประตู หลีกเลี่ยงแสงย้อนจากภายนอกอาคาร' },
                cashier: { name: 'จุดต้อนรับ / Reception', analytics: ['กิจกรรมจุดต้อนรับ'], note: 'ครอบคลุมเคาน์เตอร์ติดต่อและจุดลงทะเบียนผู้มาเยือน' },
                service: { name: 'โถงทางเดินส่วนกลาง (Corridor)', analytics: ['ภาพรวมการสัญจร'], note: 'ครอบคลุมแนวทางเดินหลักและหน้าลิฟต์' },
                seating: { name: 'พื้นที่ทำงานร่วม (Work Area)', analytics: ['Heatmap การใช้พื้นที่'], note: 'คำนึงถึงนโยบายความเป็นส่วนตัวของพนักงานก่อนติดตั้ง' },
                kitchen: { name: 'ห้องเซิร์ฟเวอร์ / ห้องเก็บเอกสาร', analytics: ['ตรวจสอบการเข้าถึงพื้นที่จำกัด'], note: 'เฝ้าระวังเฉพาะหน้าประตูห้องสำคัญ' }
            }
        }
    };

    const ANALYTICS_LABELS = {
        people_counting: 'นับคนเข้า-ออก',
        cashier_monitoring: 'กิจกรรมเคาน์เตอร์',
        service_audio: 'วิเคราะห์เสียงบริการ',
        heatmap: 'Heatmap พื้นที่',
        workflow: 'ขั้นตอนการทำงาน'
    };

    // ----------------------------------------------------------------------
    // 2. Application State
    // ----------------------------------------------------------------------
    const state = {
        layoutId: null,
        layoutName: 'แผนผังติดตั้งกล้อง TATIP AI',
        businessType: 'restaurant',
        widthM: 10,
        lengthM: 14,
        ceilingHeightM: null,
        floors: 1,
        activeFloor: 1,
        entranceCount: 1,
        entranceSide: 'front',
        zonesEnabled: {
            entrance: true,
            cashier: true,
            service: true,
            seating: true,
            kitchen: true
        },
        analyticsFocus: ['people_counting', 'cashier_monitoring', 'heatmap', 'workflow'],
        zones: [],
        cameras: [],
        selectedCameraId: null,
        highlightedZoneId: null,
        viewMode: '2d', // '2d' | '3d'
        overlays: {
            blindSpots: true,
            overlaps: false,
            zoneLabels: true
        },
        reasoning: [],
        coverageReport: null,
        savedLayouts: [],
        dragState: null
    };

    // ----------------------------------------------------------------------
    // 3. Geometric & Blind-Spot Analysis Utilities
    // ----------------------------------------------------------------------
    function clamp(val, min, max) {
        return Math.max(min, Math.min(max, val));
    }

    function normalizeAngle(deg) {
        let a = deg % 360;
        if (a < 0) a += 360;
        return Math.round(a);
    }

    function getCompassLabel(deg) {
        const d = normalizeAngle(deg);
        if (d >= 337.5 || d < 22.5) return 'ขวา (ตะวันออก)';
        if (d < 67.5) return 'ขวาล่าง (ตะวันออกเฉียงใต้)';
        if (d < 112.5) return 'ด้านล่าง/หน้าประตู (ใต้)';
        if (d < 157.5) return 'ซ้ายล่าง (ตะวันตกเฉียงใต้)';
        if (d < 202.5) return 'ซ้าย (ตะวันตก)';
        if (d < 247.5) return 'ซ้ายบน (ตะวันตกเฉียงเหนือ)';
        if (d < 292.5) return 'ด้านบน/หลังร้าน (เหนือ)';
        return 'ขวาบน (ตะวันออกเฉียงเหนือ)';
    }

    function isPointInCameraFov(px, py, cam) {
        const dx = px - cam.x_m;
        const dy = py - cam.y_m;
        const distSq = dx * dx + dy * dy;
        if (distSq > cam.range_m * cam.range_m) return false;
        if (distSq < 0.04) return true; // right under camera

        let angle = Math.atan2(dy, dx) * (180 / Math.PI);
        if (angle < 0) angle += 360;

        let diff = Math.abs(angle - cam.direction_deg);
        if (diff > 180) diff = 360 - diff;
        return diff <= (cam.fov_deg / 2);
    }

    function computeCameraFovPolygon(cam, widthM, lengthM) {
        const steps = 28;
        const halfFov = cam.fov_deg / 2;
        const startDeg = cam.direction_deg - halfFov;
        const pts = [[cam.x_m, cam.y_m]];

        for (let i = 0; i <= steps; i++) {
            const deg = startDeg + (cam.fov_deg * i) / steps;
            const rad = deg * (Math.PI / 180);
            const cos = Math.cos(rad);
            const sin = Math.sin(rad);

            // Ray-box intersection with [0, widthM] x [0, lengthM]
            let tMax = cam.range_m;
            if (Math.abs(cos) > 1e-6) {
                const tx = cos > 0 ? (widthM - cam.x_m) / cos : (0 - cam.x_m) / cos;
                if (tx >= 0) tMax = Math.min(tMax, tx);
            }
            if (Math.abs(sin) > 1e-6) {
                const ty = sin > 0 ? (lengthM - cam.y_m) / sin : (0 - cam.y_m) / sin;
                if (ty >= 0) tMax = Math.min(tMax, ty);
            }
            tMax = Math.max(0, tMax);
            pts.push([
                clamp(cam.x_m + cos * tMax, 0, widthM),
                clamp(cam.y_m + sin * tMax, 0, lengthM)
            ]);
        }
        return pts;
    }

    function analyzeCoverage() {
        const w = state.widthM;
        const l = state.lengthM;
        const floorCams = state.cameras.filter(c => c.floor === state.activeFloor);
        const floorZones = state.zones.filter(z => z.floor === state.activeFloor && z.active);

        // Adaptive grid step (~0.5m for normal shops, ~0.8m for huge warehouses so performance stays <10ms)
        const maxDim = Math.max(w, l);
        const step = maxDim > 35 ? 1.0 : (maxDim > 20 ? 0.75 : 0.5);
        const cellArea = step * step;

        const cells = [];
        let coveredCount = 0;
        let blindCount = 0;
        let overlapCount = 0;

        const perCamHits = {};
        floorCams.forEach(c => { perCamHits[c.camera_id] = 0; });

        const zoneStats = {};
        floorZones.forEach(z => {
            zoneStats[z.zone_id] = { zone: z, totalCells: 0, coveredCells: 0, overlapCells: 0 };
        });

        for (let y = step / 2; y < l; y += step) {
            for (let x = step / 2; x < w; x += step) {
                let hitCount = 0;
                for (const cam of floorCams) {
                    if (isPointInCameraFov(x, y, cam)) {
                        hitCount++;
                        perCamHits[cam.camera_id]++;
                    }
                }
                if (hitCount > 0) coveredCount++;
                else blindCount++;
                if (hitCount >= 2) overlapCount++;

                cells.push({ x, y, hitCount });

                for (const z of floorZones) {
                    if (x >= z.x_m && x <= z.x_m + z.width_m && y >= z.y_m && y <= z.y_m + z.length_m) {
                        zoneStats[z.zone_id].totalCells++;
                        if (hitCount > 0) zoneStats[z.zone_id].coveredCells++;
                        if (hitCount >= 2) zoneStats[z.zone_id].overlapCells++;
                    }
                }
            }
        }

        const totalCells = Math.max(1, cells.length);
        const totalArea = w * l;
        const coveredPct = Math.round((coveredCount / totalCells) * 100);
        const blindPct = 100 - coveredPct;
        const overlapPct = Math.round((overlapCount / totalCells) * 100);

        // Per-camera coverage m2 & pct
        floorCams.forEach(cam => {
            const hits = perCamHits[cam.camera_id] || 0;
            cam._computedCoverageM2 = Math.min(totalArea, +(hits * cellArea).toFixed(1));
            cam._computedCoveragePct = Math.min(100, Math.round((hits / totalCells) * 100));
        });

        const zoneResults = Object.values(zoneStats).map(zs => {
            const pct = zs.totalCells > 0 ? Math.round((zs.coveredCells / zs.totalCells) * 100) : 0;
            let status = 'covered';
            if (pct === 0) status = 'uncovered';
            else if (pct < 65) status = 'needs_check';
            return {
                zone_id: zs.zone.zone_id,
                name: zs.zone.name,
                category: zs.zone.category,
                coveragePct: pct,
                overlapPct: zs.totalCells > 0 ? Math.round((zs.overlapCells / zs.totalCells) * 100) : 0,
                status
            };
        });

        // Build actionable blind-spot & optimization suggestions
        const suggestions = [];
        zoneResults.forEach(zr => {
            if (zr.status === 'uncovered') {
                suggestions.push(`โซน "${zr.name}" ยังไม่มีกล้องครอบคลุม (0%) — แนะนำเพิ่มกล้องหรือปรับทิศทางกล้องใกล้เคียงหันเข้าสู่โซนนี้`);
            } else if (zr.status === 'needs_check') {
                suggestions.push(`โซน "${zr.name}" ครอบคลุมเพียง ${zr.coveragePct}% — แนะนำปรับมุมกล้องหรือย้ายตำแหน่งเพื่อลดพื้นที่อับสายตา`);
            }
        });
        if (overlapPct > 45 && floorCams.length > 2) {
            suggestions.push(`พบพื้นที่ซ้อนทับกัน ${overlapPct}% ของพื้นที่ทั้งหมด — อาจกระจายมุมกล้องออกเพื่อให้ครอบคลุมมุมห้องอื่นได้มากขึ้น`);
        }
        floorCams.forEach(cam => {
            if ((cam._computedCoveragePct || 0) < 12) {
                suggestions.push(`${cam.camera_id} (${cam.name}) ครอบคลุมพื้นที่เพียง ${cam._computedCoveragePct}% — ตรวจสอบว่ากล้องหันเข้าหาผนังหรือไม่`);
            }
        });
        if (state.cameras.length > 7) {
            suggestions.push(`จำนวนกล้องรวม (${state.cameras.length} ตัว) เกิน 7 ช่องต่อเครื่อง NVR — กรุณาตรวจสอบสเปกเครื่องบันทึกหรือแบ่งกลุ่ม NVR หน้างาน`);
        }
        if (suggestions.length === 0 && floorCams.length > 0) {
            suggestions.push('การครอบคลุมเชิงเรขาคณิตบนแผนผังอยู่ในเกณฑ์ดี — โปรดตรวจสอบสิ่งกีดขวางแนวตั้ง ระดับฝ้า และแสงย้อนหน้างานจริงก่อนติดตั้ง');
        }

        state.coverageReport = {
            step,
            cells,
            totalArea: +totalArea.toFixed(1),
            coveredPct,
            coveredAreaM2: +(totalArea * (coveredPct / 100)).toFixed(1),
            blindPct,
            blindAreaM2: +(totalArea * (blindPct / 100)).toFixed(1),
            overlapPct,
            overlapAreaM2: +(totalArea * (overlapPct / 100)).toFixed(1),
            zoneResults,
            suggestions
        };

        return state.coverageReport;
    }

    // ----------------------------------------------------------------------
    // 4. Dynamic Recommendation Engine (Space & Zone Driven)
    // ----------------------------------------------------------------------
    function buildZonesForFloor(floor, widthM, lengthM, businessType, zonesEnabled, entranceCount, entranceSide) {
        const tpl = BUSINESS_TEMPLATES[businessType] || BUSINESS_TEMPLATES.restaurant;
        const zones = [];

        // Entrance zone position based on side (front = y=lengthM, back = y=0, left = x=0, right = x=widthM)
        if (zonesEnabled.entrance && floor === 1) {
            for (let i = 0; i < entranceCount; i++) {
                let ex = widthM * ((i + 1) / (entranceCount + 1));
                let ey = lengthM - 1.5;
                let ew = Math.min(3.0, widthM * 0.35);
                let el = 1.5;
                if (entranceSide === 'back') {
                    ey = 0;
                } else if (entranceSide === 'left') {
                    ex = 0;
                    ey = lengthM * ((i + 1) / (entranceCount + 1)) - 1.2;
                    ew = 1.5;
                    el = Math.min(3.0, lengthM * 0.3);
                } else if (entranceSide === 'right') {
                    ex = Math.max(0, widthM - 1.5);
                    ey = lengthM * ((i + 1) / (entranceCount + 1)) - 1.2;
                    ew = 1.5;
                    el = Math.min(3.0, lengthM * 0.3);
                } else {
                    ex = clamp(ex - ew / 2, 0.2, Math.max(0.2, widthM - ew - 0.2));
                }
                zones.push({
                    zone_id: `zone_entrance_${floor}_${i + 1}`,
                    name: entranceCount > 1 ? `ทางเข้า ${i + 1}` : tpl.zones.entrance.name,
                    category: 'entrance',
                    floor,
                    x_m: +clamp(ex, 0, widthM - 1).toFixed(2),
                    y_m: +clamp(ey, 0, lengthM - 1).toFixed(2),
                    width_m: +ew.toFixed(2),
                    length_m: +el.toFixed(2),
                    active: true
                });
            }
        }

        // Back/Kitchen zone at top strip (0 -> ~28% length) if enabled
        const hasBack = zonesEnabled.kitchen;
        const backDepth = hasBack ? clamp(lengthM * 0.28, 2.2, 6.0) : 0;
        if (hasBack) {
            zones.push({
                zone_id: `zone_kitchen_${floor}`,
                name: tpl.zones.kitchen.name,
                category: 'kitchen',
                floor,
                x_m: 0.2,
                y_m: 0.2,
                width_m: +(widthM - 0.4).toFixed(2),
                length_m: +(backDepth - 0.3).toFixed(2),
                active: true
            });
        }

        // Cashier & Service zones on mid-upper or side strip
        const midTop = backDepth + 0.3;
        const midHeight = clamp(lengthM * 0.22, 2.0, 4.5);

        if (zonesEnabled.cashier) {
            const cw = clamp(widthM * 0.34, 2.0, 5.0);
            zones.push({
                zone_id: `zone_cashier_${floor}`,
                name: tpl.zones.cashier.name,
                category: 'cashier',
                floor,
                x_m: 0.3,
                y_m: +midTop.toFixed(2),
                width_m: +cw.toFixed(2),
                length_m: +midHeight.toFixed(2),
                active: true
            });
        }

        if (zonesEnabled.service) {
            const sx = zonesEnabled.cashier ? clamp(widthM * 0.38, 2.6, widthM * 0.5) : 0.3;
            const sw = clamp(widthM - sx - 0.3, 2.0, widthM * 0.55);
            zones.push({
                zone_id: `zone_service_${floor}`,
                name: tpl.zones.service.name,
                category: 'service',
                floor,
                x_m: +sx.toFixed(2),
                y_m: +midTop.toFixed(2),
                width_m: +sw.toFixed(2),
                length_m: +midHeight.toFixed(2),
                active: true
            });
        }

        // Seating / Retail Floor / Work Area fills main front/middle floor
        if (zonesEnabled.seating) {
            const seatingTop = (zonesEnabled.cashier || zonesEnabled.service) ? (midTop + midHeight + 0.3) : (backDepth + 0.3);
            const seatingLen = Math.max(2.0, lengthM - seatingTop - (zonesEnabled.entrance && entranceSide === 'front' ? 1.6 : 0.3));
            zones.push({
                zone_id: `zone_seating_${floor}`,
                name: tpl.zones.seating.name,
                category: 'seating',
                floor,
                x_m: 0.3,
                y_m: +seatingTop.toFixed(2),
                width_m: +(widthM - 0.6).toFixed(2),
                length_m: +seatingLen.toFixed(2),
                active: true
            });
        }

        return zones;
    }

    function createCameraObject(idNum, overrides) {
        const presetKey = overrides.camera_type || 'standard_4mp';
        const preset = CAMERA_PRESETS[presetKey] || CAMERA_PRESETS.standard_4mp;
        const pad = idNum < 10 ? `0${idNum}` : `${idNum}`;
        return {
            camera_id: `CAM-${pad}`,
            name: overrides.name || `CAM ${pad}`,
            floor: overrides.floor || 1,
            zone_id: overrides.zone_id || '',
            zone_name: overrides.zone_name || 'พื้นที่ทั่วไป',
            x_m: +Number(overrides.x_m || 1).toFixed(2),
            y_m: +Number(overrides.y_m || 1).toFixed(2),
            direction_deg: normalizeAngle(overrides.direction_deg ?? 90),
            fov_deg: overrides.fov_deg ?? preset.fov_deg,
            range_m: overrides.range_m ?? preset.range_m,
            camera_type: presetKey,
            resolution: overrides.resolution || preset.resolution,
            lens_label: overrides.lens_label || preset.lens_label,
            detection_distance: overrides.detection_distance || preset.detection_distance,
            analytics: overrides.analytics || [...preset.default_analytics],
            notes: overrides.notes || 'ตรวจสอบความสูงฝ้า สิ่งกีดขวาง และสภาพแสงหน้างานจริงก่อนติดตั้ง',
            is_custom: !!overrides.is_custom
        };
    }

    function generateRecommendedPlan() {
        const w = state.widthM;
        const l = state.lengthM;
        const floors = state.floors;
        const areaPerFloor = w * l;
        const totalArea = areaPerFloor * floors;
        const tpl = BUSINESS_TEMPLATES[state.businessType] || BUSINESS_TEMPLATES.restaurant;
        const wantsAudio = state.analyticsFocus.includes('service_audio');
        const audioUsed = { used: false };

        const allZones = [];
        const allCameras = [];
        const reasoning = [];
        let camCounter = 1;

        reasoning.push(`คำนวณจากพื้นที่จริง: กว้าง ${w} ม. × ยาว ${l} ม. (${areaPerFloor.toFixed(1)} ตร.ม./ชั้น) จำนวน ${floors} ชั้น รวม ${totalArea.toFixed(1)} ตร.ม.`);

        for (let floor = 1; floor <= floors; floor++) {
            const floorZones = buildZonesForFloor(
                floor, w, l, state.businessType,
                state.zonesEnabled, state.entranceCount, state.entranceSide
            );
            allZones.push(...floorZones);

            // 1. Entrance Cameras (Floor 1 or specified entrances)
            const entranceZones = floorZones.filter(z => z.category === 'entrance');
            entranceZones.forEach((ez, idx) => {
                let cx = ez.x_m + ez.width_m / 2;
                let cy = ez.y_m + ez.length_m / 2;
                let dir = 270; // looking inward from front
                if (state.entranceSide === 'front') {
                    cy = Math.max(0.4, ez.y_m - 1.2);
                    dir = 90; // looking straight at entrance door from inside
                } else if (state.entranceSide === 'back') {
                    cy = Math.min(l - 0.4, ez.y_m + ez.length_m + 1.2);
                    dir = 270;
                } else if (state.entranceSide === 'left') {
                    cx = Math.min(w - 0.4, ez.x_m + ez.width_m + 1.2);
                    dir = 180;
                } else if (state.entranceSide === 'right') {
                    cx = Math.max(0.4, ez.x_m - 1.2);
                    dir = 0;
                }
                allCameras.push(createCameraObject(camCounter++, {
                    name: entranceZones.length > 1 ? `Entrance ${idx + 1}` : 'Entrance',
                    floor,
                    zone_id: ez.zone_id,
                    zone_name: ez.name,
                    x_m: cx,
                    y_m: cy,
                    direction_deg: dir,
                    camera_type: 'focused_4mp',
                    range_m: clamp(Math.max(w, l) * 0.55, 8, 15),
                    analytics: ['นับคนเข้า-ออก', 'บันทึกภาพทางเข้า'],
                    notes: tpl.zones.entrance.note
                }));
            });

            // 2. Cashier Camera
            const cashierZone = floorZones.find(z => z.category === 'cashier');
            if (cashierZone) {
                allCameras.push(createCameraObject(camCounter++, {
                    name: floors > 1 ? `Cashier (ชั้น ${floor})` : 'Cashier',
                    floor,
                    zone_id: cashierZone.zone_id,
                    zone_name: cashierZone.name,
                    x_m: clamp(cashierZone.x_m + 0.3, 0.3, w - 0.3),
                    y_m: clamp(cashierZone.y_m + 0.3, 0.3, l - 0.3),
                    direction_deg: 45,
                    camera_type: 'standard_4mp',
                    range_m: clamp(Math.max(cashierZone.width_m, cashierZone.length_m) * 1.9, 6, 12),
                    analytics: ['กิจกรรมจุดชำระเงิน', 'ตรวจสอบการทอนเงิน/รับสินค้า'],
                    notes: tpl.zones.cashier.note
                }));
            }

            // 3. Service Area Camera (supports 1 audio camera per branch if requested)
            const serviceZone = floorZones.find(z => z.category === 'service');
            if (serviceZone) {
                const assignAudio = wantsAudio && !audioUsed.used;
                if (assignAudio) audioUsed.used = true;
                allCameras.push(createCameraObject(camCounter++, {
                    name: floors > 1 ? `Service Area (ชั้น ${floor})` : 'Service Area',
                    floor,
                    zone_id: serviceZone.zone_id,
                    zone_name: serviceZone.name,
                    x_m: clamp(serviceZone.x_m + serviceZone.width_m - 0.3, 0.3, w - 0.3),
                    y_m: clamp(serviceZone.y_m + 0.3, 0.3, l - 0.3),
                    direction_deg: 135,
                    camera_type: assignAudio ? 'audio_cam' : 'standard_4mp',
                    range_m: clamp(Math.max(serviceZone.width_m, serviceZone.length_m) * 1.8, 7, 12),
                    analytics: assignAudio
                        ? ['วิเคราะห์เสียงสนทนาบริการ', 'ตรวจสอบขั้นตอนบริการ']
                        : ['ตรวจสอบขั้นตอนบริการ', 'ความเร็วการให้บริการ'],
                    notes: tpl.zones.service.note + (assignAudio ? ' (กำหนดเป็นกล้องวิเคราะห์เสียง 1 ตัวของสาขา)' : '')
                }));
            }

            // 4. Open Floor / Seating / Display Area — Dynamically scaled by Zone Width & Length!
            const seatingZone = floorZones.find(z => z.category === 'seating');
            if (seatingZone) {
                const cols = Math.max(1, Math.ceil(seatingZone.width_m / 9.5));
                const rows = Math.max(1, Math.ceil(seatingZone.length_m / 8.5));
                const neededFloorCams = cols * rows;

                let floorIdx = 1;
                for (let r = 0; r < rows; r++) {
                    for (let c = 0; c < cols; c++) {
                        // Place cameras on alternating corners or side walls of their sub-cell
                        const subX = seatingZone.x_m + (seatingZone.width_m * c) / cols;
                        const subW = seatingZone.width_m / cols;
                        const subY = seatingZone.y_m + (seatingZone.length_m * r) / rows;
                        const subL = seatingZone.length_m / rows;

                        const onLeft = c === 0;
                        const cx = onLeft ? Math.max(0.35, subX) : Math.min(w - 0.35, subX + subW);
                        const cy = Math.max(0.35, subY + 0.3);
                        const dir = onLeft ? 50 : 130;

                        const rangeNeeded = clamp(Math.hypot(subW, subL) * 0.95, 8, 14);
                        allCameras.push(createCameraObject(camCounter++, {
                            name: neededFloorCams > 1 ? `Floor/Seating ${floorIdx}` : 'Dining / Floor Area',
                            floor,
                            zone_id: seatingZone.zone_id,
                            zone_name: seatingZone.name,
                            x_m: cx,
                            y_m: cy,
                            direction_deg: dir,
                            camera_type: 'wide_4mp',
                            range_m: +rangeNeeded.toFixed(1),
                            analytics: ['Heatmap ความหนาแน่น', 'นับจำนวนลูกค้าในพื้นที่'],
                            notes: tpl.zones.seating.note
                        }));
                        floorIdx++;
                    }
                }
                reasoning.push(`โซน "${seatingZone.name}" ชั้น ${floor} ขนาด ${seatingZone.width_m}×${seatingZone.length_m} ม. (${(seatingZone.width_m * seatingZone.length_m).toFixed(0)} ตร.ม.) ➔ คำนวณกล้องมุมกว้าง ${neededFloorCams} ตัว (แบ่งช่วงระยะ ${cols}×${rows})`);
            }

            // 5. Kitchen / Back-of-house / Stock Area — Scaled dynamically if wide
            const kitchenZone = floorZones.find(z => z.category === 'kitchen');
            if (kitchenZone) {
                const kCols = Math.max(1, Math.ceil(kitchenZone.width_m / 11.0));
                for (let k = 0; k < kCols; k++) {
                    const cx = k === 0 ? 0.35 : clamp(kitchenZone.x_m + (kitchenZone.width_m * (k + 0.9)) / kCols, 0.35, w - 0.35);
                    const cy = 0.35;
                    const dir = k === 0 ? 35 : 145;
                    allCameras.push(createCameraObject(camCounter++, {
                        name: kCols > 1 ? `Back/Kitchen ${k + 1}` : 'Kitchen / Back Area',
                        floor,
                        zone_id: kitchenZone.zone_id,
                        zone_name: kitchenZone.name,
                        x_m: cx,
                        y_m: cy,
                        direction_deg: dir,
                        camera_type: 'standard_4mp',
                        range_m: clamp(Math.max(kitchenZone.width_m / kCols, kitchenZone.length_m) * 1.15, 7, 13),
                        analytics: ['ตรวจสอบกระบวนการทำงาน', 'ความปลอดภัยหลังร้าน'],
                        notes: tpl.zones.kitchen.note
                    }));
                }
            }
        }

        // Fallback: if user unchecked ALL zones, still require minimum 1 overview camera per TATIP rule
        if (allCameras.length === 0) {
            allCameras.push(createCameraObject(1, {
                name: 'Main Overview',
                floor: 1,
                zone_name: 'ภาพรวมพื้นที่ร้าน',
                x_m: 0.4,
                y_m: 0.4,
                direction_deg: 45,
                camera_type: 'wide_4mp',
                range_m: clamp(Math.hypot(w, l) * 0.85, 8, 16),
                analytics: ['ภาพรวมพื้นที่'],
                notes: 'ระบบ TATIP ต้องมีกล้องเชื่อมต่ออย่างน้อย 1 ตัวจึงจะใช้งานฟังก์ชันหลักได้'
            }));
            reasoning.push('ไม่ได้เลือกโซนเฉพาะ ➔ แนะนำกล้องมุมกว้างอย่างน้อย 1 ตัวตามข้อกำหนดขั้นต่ำของระบบ TATIP');
        }

        if (wantsAudio) {
            reasoning.push('เลือกวิเคราะห์เสียงสนทนา ➔ กำหนดกล้องเสียง 1 ตัวตามมาตรฐาน TATIP (จำกัด 1 ตัวต่อสาขา)');
        }
        if (allCameras.length > 7) {
            reasoning.push(`ข้อควรตรวจสอบ: แผนเบื้องต้นแนะนำ ${allCameras.length} ตัว ซึ่งเกิน 7 ช่องต่อเครื่อง NVR ของระบบ TATIP ต้องตรวจสอบการแบ่งเครื่องบันทึกหน้างาน`);
        }

        state.zones = allZones;
        state.cameras = allCameras;
        state.activeFloor = 1;
        state.selectedCameraId = allCameras[0]?.camera_id || null;
        state.reasoning = reasoning;

        analyzeCoverage();
        renderAll();
    }

    // ----------------------------------------------------------------------
    // 5. 2D Interactive SVG Floor Plan Renderer
    // ----------------------------------------------------------------------
    function render2DFloorPlan() {
        const container = document.getElementById('plannerCanvasContainer');
        if (!container) return;

        const w = state.widthM;
        const l = state.lengthM;
        const pad = 1.4; // meters padding around walls for rulers & labels
        const vbW = w + pad * 2;
        const vbH = l + pad * 2;

        const floorCams = state.cameras.filter(c => c.floor === state.activeFloor);
        const floorZones = state.zones.filter(z => z.floor === state.activeFloor && z.active);
        const report = state.coverageReport || analyzeCoverage();

        const zoneColors = {
            entrance: { fill: '#FEE2E2', stroke: '#EF4444', text: '#991B1B' },
            cashier: { fill: '#FEF3C7', stroke: '#F59E0B', text: '#92400E' },
            service: { fill: '#DBEAFE', stroke: '#3B82F6', text: '#1E40AF' },
            seating: { fill: '#DCFCE7', stroke: '#22C55E', text: '#166534' },
            kitchen: { fill: '#F1F5F9', stroke: '#64748B', text: '#334155' }
        };

        let svg = `<svg id="floorPlanSvg" viewBox="${-pad} ${-pad} ${vbW} ${vbH}" style="width:100%;height:100%;max-height:540px;display:block;user-select:none;" xmlns="http://www.w3.org/2000/svg">`;

        // Defs: Clip path to room interior, hatch pattern for blind spots
        svg += `
        <defs>
            <clipPath id="roomInteriorClip">
                <rect x="0" y="0" width="${w}" height="${l}" rx="0.1" />
            </clipPath>
            <pattern id="blindSpotPattern" width="0.5" height="0.5" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">
                <line x1="0" y1="0" x2="0" y2="0.5" stroke="rgba(220, 38, 38, 0.25)" stroke-width="0.12" />
            </pattern>
        </defs>`;

        // Outer floor backdrop
        svg += `<rect x="0" y="0" width="${w}" height="${l}" fill="#FFFFFF" stroke="#0F172A" stroke-width="0.14" />`;

        // 1m Grid lines
        for (let gx = 1; gx < w; gx++) {
            svg += `<line x1="${gx}" y1="0" x2="${gx}" y2="${l}" stroke="#E2E8F0" stroke-width="0.03" />`;
        }
        for (let gy = 1; gy < l; gy++) {
            svg += `<line x1="0" y1="${gy}" x2="${w}" y2="${gy}" stroke="#E2E8F0" stroke-width="0.03" />`;
        }

        // Ruler ticks (every 2m or 1m if small)
        const tickStep = Math.max(w, l) > 18 ? 4 : 2;
        for (let tx = 0; tx <= w; tx += tickStep) {
            svg += `<text x="${tx}" y="${-0.35}" font-size="0.42" fill="#64748B" text-anchor="middle">${tx}ม.</text>`;
        }
        for (let ty = 0; ty <= l; ty += tickStep) {
            svg += `<text x="${-0.45}" y="${ty + 0.14}" font-size="0.42" fill="#64748B" text-anchor="end">${ty}ม.</text>`;
        }

        // Zones Layer
        floorZones.forEach(z => {
            const c = zoneColors[z.category] || zoneColors.kitchen;
            const isHl = state.highlightedZoneId === z.zone_id;
            svg += `<rect data-zone-id="${z.zone_id}" x="${z.x_m}" y="${z.y_m}" width="${z.width_m}" height="${z.length_m}" rx="0.18"
                fill="${c.fill}" fill-opacity="${isHl ? '0.68' : '0.45'}"
                stroke="${c.stroke}" stroke-width="${isHl ? '0.1' : '0.05'}" stroke-dasharray="${isHl ? 'none' : '0.2,0.12'}"
                style="cursor:pointer;" />`;
            if (state.overlays.zoneLabels) {
                svg += `<text x="${z.x_m + z.width_m / 2}" y="${z.y_m + z.length_m / 2}" font-size="0.44" font-weight="600"
                    fill="${c.text}" text-anchor="middle" dominant-baseline="middle" pointer-events="none">${escapeHtml(z.name.split('(')[0].trim())}</text>`;
            }
        });

        // Blind Spot & Overlap Grid Overlay (clipped to room)
        if (state.overlays.blindSpots || state.overlays.overlaps) {
            svg += `<g clip-path="url(#roomInteriorClip)" pointer-events="none">`;
            const hs = report.step / 2;
            report.cells.forEach(cell => {
                if (state.overlays.blindSpots && cell.hitCount === 0) {
                    svg += `<rect x="${cell.x - hs}" y="${cell.y - hs}" width="${report.step}" height="${report.step}" fill="url(#blindSpotPattern)" />`;
                    svg += `<rect x="${cell.x - hs}" y="${cell.y - hs}" width="${report.step}" height="${report.step}" fill="rgba(239, 68, 68, 0.08)" />`;
                } else if (state.overlays.overlaps && cell.hitCount >= 2) {
                    svg += `<rect x="${cell.x - hs}" y="${cell.y - hs}" width="${report.step}" height="${report.step}" fill="rgba(37, 99, 235, 0.14)" />`;
                }
            });
            svg += `</g>`;
        }

        // Camera FOV Cones Layer (clipped to room)
        svg += `<g clip-path="url(#roomInteriorClip)">`;
        floorCams.forEach(cam => {
            const isSel = cam.camera_id === state.selectedCameraId;
            const polyPts = computeCameraFovPolygon(cam, w, l)
                .map(pt => `${pt[0].toFixed(2)},${pt[1].toFixed(2)}`)
                .join(' ');
            const fill = isSel ? 'rgba(200, 30, 30, 0.22)' : 'rgba(200, 30, 30, 0.10)';
            const stroke = isSel ? 'rgba(200, 30, 30, 0.75)' : 'rgba(200, 30, 30, 0.35)';
            svg += `<polygon points="${polyPts}" fill="${fill}" stroke="${stroke}" stroke-width="${isSel ? '0.07' : '0.04'}" pointer-events="none" />`;
        });
        svg += `</g>`;

        // Entrance Architectural Door Symbol on Perimeter
        if (state.zonesEnabled.entrance && state.activeFloor === 1) {
            for (let i = 0; i < state.entranceCount; i++) {
                let dx = w * ((i + 1) / (state.entranceCount + 1));
                let dy = l;
                if (state.entranceSide === 'back') dy = 0;
                else if (state.entranceSide === 'left') { dx = 0; dy = l * ((i + 1) / (state.entranceCount + 1)); }
                else if (state.entranceSide === 'right') { dx = w; dy = l * ((i + 1) / (state.entranceCount + 1)); }

                if (state.entranceSide === 'front' || state.entranceSide === 'back') {
                    svg += `<line x1="${dx - 0.7}" y1="${dy}" x2="${dx + 0.7}" y2="${dy}" stroke="#FFFFFF" stroke-width="0.22" />`;
                    svg += `<line x1="${dx - 0.7}" y1="${dy}" x2="${dx + 0.7}" y2="${dy}" stroke="#C81E1E" stroke-width="0.1" stroke-dasharray="0.2,0.1" />`;
                    svg += `<text x="${dx}" y="${dy + (state.entranceSide === 'front' ? 0.55 : -0.45)}" font-size="0.38" font-weight="700" fill="#C81E1E" text-anchor="middle">ทางเข้า ${state.entranceCount > 1 ? i + 1 : ''}</text>`;
                } else {
                    svg += `<line x1="${dx}" y1="${dy - 0.7}" x2="${dx}" y2="${dy + 0.7}" stroke="#FFFFFF" stroke-width="0.22" />`;
                    svg += `<line x1="${dx}" y1="${dy - 0.7}" x2="${dx}" y2="${dy + 0.7}" stroke="#C81E1E" stroke-width="0.1" stroke-dasharray="0.2,0.1" />`;
                }
            }
        }

        // Camera Nodes & Interactive Rotation Handles
        floorCams.forEach(cam => {
            const isSel = cam.camera_id === state.selectedCameraId;
            const rad = cam.direction_deg * (Math.PI / 180);
            const arrowDist = isSel ? 1.35 : 0.95;
            const hx = clamp(cam.x_m + Math.cos(rad) * arrowDist, -0.8, w + 0.8);
            const hy = clamp(cam.y_m + Math.sin(rad) * arrowDist, -0.8, l + 0.8);

            // Direction line
            svg += `<line x1="${cam.x_m}" y1="${cam.y_m}" x2="${hx}" y2="${hy}" stroke="${isSel ? '#991B1B' : '#DC2626'}" stroke-width="${isSel ? '0.08' : '0.05'}" pointer-events="none" />`;

            // Rotation handle when selected
            if (isSel) {
                svg += `<circle data-rotate-cam="${cam.camera_id}" cx="${hx}" cy="${hy}" r="0.28" fill="#FFFFFF" stroke="#C81E1E" stroke-width="0.08" style="cursor:grab;" />`;
                svg += `<circle cx="${hx}" cy="${hy}" r="0.09" fill="#C81E1E" pointer-events="none" />`;
            }

            // Camera Pin Body
            const rPin = isSel ? 0.42 : 0.34;
            svg += `<g data-drag-cam="${cam.camera_id}" style="cursor:move;">`;
            if (isSel) {
                svg += `<circle cx="${cam.x_m}" cy="${cam.y_m}" r="0.62" fill="rgba(200,30,30,0.18)" />`;
            }
            svg += `<circle cx="${cam.x_m}" cy="${cam.y_m}" r="${rPin}" fill="${isSel ? '#991B1B' : '#C81E1E'}" stroke="#FFFFFF" stroke-width="0.09" />`;
            svg += `<text x="${cam.x_m}" y="${cam.y_m + 0.04}" font-size="0.26" font-weight="700" fill="#FFFFFF" text-anchor="middle" dominant-baseline="middle" pointer-events="none">${cam.camera_id.replace('CAM-', '')}</text>`;
            svg += `</g>`;

            // Camera Label Pill below/above pin
            const labelY = cam.y_m > l - 1.1 ? cam.y_m - 0.62 : cam.y_m + 0.72;
            svg += `<text x="${cam.x_m}" y="${labelY}" font-size="0.34" font-weight="700" fill="#0F172A" text-anchor="middle" pointer-events="none">${escapeHtml(cam.camera_id)}</text>`;
        });

        svg += `</svg>`;
        container.innerHTML = svg;
        attach2DInteractionHandlers();
    }

    function attach2DInteractionHandlers() {
        const svg = document.getElementById('floorPlanSvg');
        if (!svg) return;

        function clientToMeters(evt) {
            const pt = svg.createSVGPoint();
            pt.x = evt.clientX;
            pt.y = evt.clientY;
            const ctm = svg.getScreenCTM();
            if (!ctm) return { x: 0, y: 0 };
            const transformed = pt.matrixTransform(ctm.inverse());
            return { x: transformed.x, y: transformed.y };
        }

        svg.querySelectorAll('[data-drag-cam]').forEach(el => {
            el.addEventListener('mousedown', evt => {
                evt.stopPropagation();
                const camId = el.getAttribute('data-drag-cam');
                selectCamera(camId);
                state.dragState = { mode: 'move', camId };
            });
        });

        svg.querySelectorAll('[data-rotate-cam]').forEach(el => {
            el.addEventListener('mousedown', evt => {
                evt.stopPropagation();
                const camId = el.getAttribute('data-rotate-cam');
                state.dragState = { mode: 'rotate', camId };
            });
        });

        svg.querySelectorAll('[data-zone-id]').forEach(el => {
            el.addEventListener('click', () => {
                const zid = el.getAttribute('data-zone-id');
                state.highlightedZoneId = (state.highlightedZoneId === zid) ? null : zid;
                render2DFloorPlan();
                renderBlindSpotSummary();
            });
        });

        svg.onmousemove = evt => {
            if (!state.dragState) return;
            const cam = state.cameras.find(c => c.camera_id === state.dragState.camId);
            if (!cam) return;
            const m = clientToMeters(evt);

            if (state.dragState.mode === 'move') {
                cam.x_m = +clamp(Math.round(m.x * 4) / 4, 0.2, state.widthM - 0.2).toFixed(2);
                cam.y_m = +clamp(Math.round(m.y * 4) / 4, 0.2, state.lengthM - 0.2).toFixed(2);
            } else if (state.dragState.mode === 'rotate') {
                let deg = Math.atan2(m.y - cam.y_m, m.x - cam.x_m) * (180 / Math.PI);
                cam.direction_deg = normalizeAngle(deg);
            }
            analyzeCoverage();
            render2DFloorPlan();
            renderCameraDetailPanel();
            renderBlindSpotSummary();
            renderSummaryDashboard();
        };

        const endDrag = () => { state.dragState = null; };
        svg.onmouseup = endDrag;
        svg.onmouseleave = endDrag;
    }

    // ----------------------------------------------------------------------
    // 6. Crisp Vector 3D Isometric Architectural Renderer
    // ----------------------------------------------------------------------
    function isoProject(x, y, z, originX, originY, scale) {
        // Standard 30-degree isometric projection
        const cos30 = 0.8660254;
        const sin30 = 0.5;
        const sx = originX + (x - y) * cos30 * scale;
        const sy = originY + (x + y) * sin30 * scale - z * scale;
        return [sx, sy];
    }

    function render3DIsometricView() {
        const container = document.getElementById('plannerCanvasContainer');
        if (!container) return;

        const w = state.widthM;
        const l = state.lengthM;
        const wallH = state.ceilingHeightM ? clamp(state.ceilingHeightM, 2.4, 5.5) : 3.0;
        const mountH = wallH - 0.25;

        const svgW = 900;
        const svgH = 540;
        const spanX = (w + l) * 0.866;
        const spanY = (w + l) * 0.5 + wallH + 1.5;
        const scale = Math.min((svgW - 120) / spanX, (svgH - 90) / spanY);
        const originX = svgW / 2 + (l - w) * 0.433 * scale;
        const originY = 55 + wallH * scale;

        const p = (x, y, z = 0) => isoProject(x, y, z, originX, originY, scale);
        const ptsStr = arr => arr.map(pt => `${pt[0].toFixed(1)},${pt[1].toFixed(1)}`).join(' ');

        const floorCams = state.cameras.filter(c => c.floor === state.activeFloor);
        const floorZones = state.zones.filter(z => z.floor === state.activeFloor && z.active);

        let svg = `<svg viewBox="0 0 ${svgW} ${svgH}" style="width:100%;height:100%;max-height:540px;display:block;background:linear-gradient(180deg,#F8FAFC,#EEF2F7);border-radius:12px;" xmlns="http://www.w3.org/2000/svg">`;

        // Floor Slab
        const f0 = p(0, 0, 0), f1 = p(w, 0, 0), f2 = p(w, l, 0), f3 = p(0, l, 0);
        svg += `<polygon points="${ptsStr([f0, f1, f2, f3])}" fill="#FFFFFF" stroke="#94A3B8" stroke-width="1.5" />`;

        // Floor Grid lines
        for (let gx = 2; gx < w; gx += 2) {
            const a = p(gx, 0, 0), b = p(gx, l, 0);
            svg += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#E2E8F0" stroke-width="1" />`;
        }
        for (let gy = 2; gy < l; gy += 2) {
            const a = p(0, gy, 0), b = p(w, gy, 0);
            svg += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#E2E8F0" stroke-width="1" />`;
        }

        // Back Left Wall (x=0, y=0..l) and Back Right Wall (y=0, x=0..w)
        const wTop0 = p(0, 0, wallH), wTop1 = p(w, 0, wallH), wTop3 = p(0, l, wallH);
        svg += `<polygon points="${ptsStr([f0, f3, wTop3, wTop0])}" fill="#F1F5F9" stroke="#CBD5E1" stroke-width="1.4" />`;
        svg += `<polygon points="${ptsStr([f0, f1, wTop1, wTop0])}" fill="#E2E8F0" stroke="#CBD5E1" stroke-width="1.4" />`;

        // Zones as clean 3D floor pads / low counters
        const zStyles = {
            entrance: '#FECACA',
            cashier: '#FDE68A',
            service: '#BFDBFE',
            seating: '#BBF7D0',
            kitchen: '#E2E8F0'
        };
        floorZones.forEach(z => {
            const zh = (z.category === 'cashier' || z.category === 'service') ? 0.9 : 0.08;
            const b0 = p(z.x_m, z.y_m, 0), b1 = p(z.x_m + z.width_m, z.y_m, 0);
            const b2 = p(z.x_m + z.width_m, z.y_m + z.length_m, 0), b3 = p(z.x_m, z.y_m + z.length_m, 0);
            const t0 = p(z.x_m, z.y_m, zh), t1 = p(z.x_m + z.width_m, z.y_m, zh);
            const t2 = p(z.x_m + z.width_m, z.y_m + z.length_m, zh), t3 = p(z.x_m, z.y_m + z.length_m, zh);

            const col = zStyles[z.category] || '#E2E8F0';
            if (zh > 0.2) {
                svg += `<polygon points="${ptsStr([b3, b2, t2, t3])}" fill="${col}" fill-opacity="0.7" stroke="#94A3B8" stroke-width="1" />`;
                svg += `<polygon points="${ptsStr([b1, b2, t2, t1])}" fill="${col}" fill-opacity="0.55" stroke="#94A3B8" stroke-width="1" />`;
            }
            svg += `<polygon points="${ptsStr([t0, t1, t2, t3])}" fill="${col}" fill-opacity="0.75" stroke="#64748B" stroke-width="1" />`;
            const center = p(z.x_m + z.width_m / 2, z.y_m + z.length_m / 2, zh + 0.05);
            svg += `<text x="${center[0]}" y="${center[1]}" font-size="11" font-weight="600" fill="#1E293B" text-anchor="middle">${escapeHtml(z.name.split('(')[0].trim())}</text>`;
        });

        // Floor-projected FOV Cones
        floorCams.forEach(cam => {
            const isSel = cam.camera_id === state.selectedCameraId;
            const poly3d = computeCameraFovPolygon(cam, w, l).map(pt => p(pt[0], pt[1], 0.03));
            svg += `<polygon points="${ptsStr(poly3d)}" fill="${isSel ? 'rgba(200,30,30,0.24)' : 'rgba(200,30,30,0.11)'}" stroke="${isSel ? '#C81E1E' : 'rgba(200,30,30,0.4)'}" stroke-width="${isSel ? 2 : 1}" />`;
        });

        // Elevated 3D Camera Mounts + Drop Lines + 3D Frustum Rays
        floorCams.forEach(cam => {
            const isSel = cam.camera_id === state.selectedCameraId;
            const basePt = p(cam.x_m, cam.y_m, 0);
            const camPt = p(cam.x_m, cam.y_m, mountH);

            const rad = cam.direction_deg * (Math.PI / 180);
            const aimFloor = p(
                clamp(cam.x_m + Math.cos(rad) * Math.min(4, cam.range_m), 0, w),
                clamp(cam.y_m + Math.sin(rad) * Math.min(4, cam.range_m), 0, l),
                0.2
            );

            // Vertical mounting guide line
            svg += `<line x1="${camPt[0]}" y1="${camPt[1]}" x2="${basePt[0]}" y2="${basePt[1]}" stroke="#94A3B8" stroke-width="1.2" stroke-dasharray="3,3" />`;
            svg += `<circle cx="${basePt[0]}" cy="${basePt[1]}" r="3" fill="#64748B" />`;

            // 3D sighting ray from elevated camera to floor
            svg += `<line x1="${camPt[0]}" y1="${camPt[1]}" x2="${aimFloor[0]}" y2="${aimFloor[1]}" stroke="${isSel ? '#C81E1E' : '#EF4444'}" stroke-width="${isSel ? 2.4 : 1.5}" stroke-dasharray="4,2" />`;

            // Camera 3D Node Badge (clickable)
            svg += `<g data-select-3d="${cam.camera_id}" style="cursor:pointer;">`;
            if (isSel) {
                svg += `<circle cx="${camPt[0]}" cy="${camPt[1]}" r="18" fill="rgba(200,30,30,0.2)" />`;
            }
            svg += `<circle cx="${camPt[0]}" cy="${camPt[1]}" r="13" fill="${isSel ? '#991B1B' : '#C81E1E'}" stroke="#FFFFFF" stroke-width="2.5" />`;
            svg += `<text x="${camPt[0]}" y="${camPt[1] + 4}" font-size="10" font-weight="700" fill="#FFFFFF" text-anchor="middle">${cam.camera_id.replace('CAM-', '')}</text>`;
            svg += `<rect x="${camPt[0] - 26}" y="${camPt[1] - 32}" width="52" height="16" rx="4" fill="#0F172A" />`;
            svg += `<text x="${camPt[0]}" y="${camPt[1] - 21}" font-size="9.5" font-weight="700" fill="#FFFFFF" text-anchor="middle">${escapeHtml(cam.camera_id)}</text>`;
            svg += `</g>`;
        });

        // Dimensions caption
        svg += `<text x="16" y="${svgH - 14}" font-size="11.5" fill="#475569" font-weight="600">มุมมอง 3D Isometric จำลอง (${w} ม. × ${l} ม. · ความสูงผนังอ้างอิง ${wallH.toFixed(1)} ม.${state.ceilingHeightM ? '' : ' [Pending Verification]'})</text>`;
        svg += `</svg>`;

        container.innerHTML = svg;
        container.querySelectorAll('[data-select-3d]').forEach(el => {
            el.addEventListener('click', () => {
                selectCamera(el.getAttribute('data-select-3d'));
            });
        });
    }

    // ----------------------------------------------------------------------
    // 7. UI Panel Rendering (Detail Panel, Blind Spots, Summary Dashboard)
    // ----------------------------------------------------------------------
    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[ch]);
    }

    function renderCanvas() {
        const btn2d = document.getElementById('btnView2D');
        const btn3d = document.getElementById('btnView3D');
        if (btn2d) btn2d.classList.toggle('active', state.viewMode === '2d');
        if (btn3d) btn3d.classList.toggle('active', state.viewMode === '3d');

        if (state.viewMode === '3d') render3DIsometricView();
        else render2DFloorPlan();
    }

    function renderCameraDetailPanel() {
        const panel = document.getElementById('cameraDetailContent');
        if (!panel) return;

        const cam = state.cameras.find(c => c.camera_id === state.selectedCameraId);
        if (!cam) {
            panel.innerHTML = `<div style="text-align:center;padding:36px 16px;color:var(--text-muted);font-size:0.88rem;">
                <i class="fa-solid fa-video" style="font-size:1.8rem;color:#CBD5E1;margin-bottom:10px;display:block;"></i>
                คลิกที่กล้องบนแผนผัง 2D/3D หรือในตารางด้านล่างเพื่อดูและแก้ไขรายละเอียด
            </div>`;
            return;
        }

        const floorZones = state.zones.filter(z => z.floor === cam.floor && z.active);
        const zoneOptions = floorZones.map(z =>
            `<option value="${escapeHtml(z.zone_id)}" ${cam.zone_id === z.zone_id ? 'selected' : ''}>${escapeHtml(z.name)}</option>`
        ).join('') + `<option value="" ${!cam.zone_id ? 'selected' : ''}>พื้นที่ทั่วไป / กำหนดเอง</option>`;

        const presetOptions = Object.entries(CAMERA_PRESETS).map(([k, p]) =>
            `<option value="${k}" ${cam.camera_type === k ? 'selected' : ''}>${escapeHtml(p.label)}</option>`
        ).join('');

        const heightText = state.ceilingHeightM ? `${state.ceilingHeightM} ม. (ระดับฝ้าที่ระบุ)` : 'Pending Verification (รอยืนยันความสูงหน้างาน)';
        const analyticsBadges = (cam.analytics || []).map(a =>
            `<span style="display:inline-block;background:#FEF2F2;color:#B91C1C;border:1px solid #FECACA;border-radius:99px;padding:2px 9px;font-size:0.74rem;font-weight:600;margin:2px;">${escapeHtml(a)}</span>`
        ).join('');

        panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--border-color);">
                <div>
                    <span style="background:#C81E1E;color:#fff;padding:2px 8px;border-radius:6px;font-size:0.76rem;font-weight:700;">${escapeHtml(cam.camera_id)}</span>
                    <span style="font-size:0.78rem;color:var(--text-muted);margin-left:6px;">ชั้น ${cam.floor}</span>
                </div>
                <button type="button" onclick="CameraPlanner.deleteCamera('${escapeHtml(cam.camera_id)}')" class="btn-saas btn-saas-danger-soft" style="height:30px;padding:0 10px;font-size:0.76rem;">
                    <i class="fa-solid fa-trash-can"></i> ลบกล้อง
                </button>
            </div>

            <div class="form-group" style="margin-bottom:10px;">
                <label style="font-size:0.78rem;">ชื่อกล้อง (Camera Name)</label>
                <input type="text" id="detailCamName" value="${escapeHtml(cam.name)}" style="padding:7px 10px;font-size:0.84rem;" onchange="CameraPlanner.updateSelectedCameraField('name', this.value)">
            </div>

            <div class="form-group" style="margin-bottom:10px;">
                <label style="font-size:0.78rem;">โซนติดตั้ง (Installation Zone)</label>
                <select id="detailCamZone" style="padding:7px 10px;font-size:0.84rem;" onchange="CameraPlanner.updateSelectedCameraZone(this.value)">${zoneOptions}</select>
            </div>

            <div class="form-group" style="margin-bottom:10px;">
                <label style="font-size:0.78rem;">ประเภทกล้อง / เลนส์ (Camera Preset)</label>
                <select id="detailCamPreset" style="padding:7px 10px;font-size:0.84rem;" onchange="CameraPlanner.updateSelectedCameraPreset(this.value)">${presetOptions}</select>
            </div>

            <div class="form-group" style="margin-bottom:10px;">
                <label style="font-size:0.78rem;display:flex;justify-content:space-between;">
                    <span>ทิศทางการมอง (Camera Direction)</span>
                    <strong style="color:var(--red);">${cam.direction_deg}° · ${getCompassLabel(cam.direction_deg)}</strong>
                </label>
                <input type="range" min="0" max="359" value="${cam.direction_deg}" style="padding:0;accent-color:#C81E1E;" oninput="CameraPlanner.updateSelectedCameraDirection(this.value)">
            </div>

            <div style="background:#F8FAFC;border:1px solid var(--border-color);border-radius:10px;padding:10px 12px;font-size:0.8rem;display:grid;gap:6px;margin-bottom:10px;">
                <div><strong>ตำแหน่งติดตั้ง:</strong> X: ${cam.x_m} ม., Y: ${cam.y_m} ม. <span style="color:var(--text-muted);">(${heightText})</span></div>
                <div><strong>ความละเอียดที่แนะนำ:</strong> ${escapeHtml(cam.resolution)}</div>
                <div><strong>เลนส์ / มุมมอง:</strong> ${escapeHtml(cam.lens_label)}</div>
                <div><strong>ระยะตรวจจับ:</strong> ${escapeHtml(cam.detection_distance)}</div>
                <div><strong>พื้นที่ครอบคลุมกล้องนี้:</strong> ~${cam._computedCoverageM2 ?? 0} ตร.ม. (${cam._computedCoveragePct ?? 0}% ของชั้น)</div>
            </div>

            <div style="margin-bottom:10px;">
                <div style="font-size:0.78rem;font-weight:600;color:#334155;margin-bottom:4px;">AI Analytics ที่รองรับ:</div>
                <div>${analyticsBadges}</div>
            </div>

            <div class="form-group" style="margin-bottom:0;">
                <label style="font-size:0.78rem;">หมายเหตุการติดตั้ง (Installation Notes)</label>
                <textarea rows="2" style="padding:7px 10px;font-size:0.8rem;" onchange="CameraPlanner.updateSelectedCameraField('notes', this.value)">${escapeHtml(cam.notes)}</textarea>
            </div>
        `;
    }

    function renderBlindSpotSummary() {
        const box = document.getElementById('blindSpotPanel');
        if (!box || !state.coverageReport) return;

        const r = state.coverageReport;
        const zoneRows = r.zoneResults.map(zr => {
            let badgeBg = '#DCFCE7', badgeColor = '#166534', statusTxt = 'ครอบคลุมดี';
            if (zr.status === 'uncovered') {
                badgeBg = '#FEE2E2'; badgeColor = '#991B1B'; statusTxt = 'จุดอับ (0%)';
            } else if (zr.status === 'needs_check') {
                badgeBg = '#FEF3C7'; badgeColor = '#92400E'; statusTxt = `ต้องตรวจสอบ (${zr.coveragePct}%)`;
            }
            return `<div onclick="CameraPlanner.toggleZoneHighlight('${escapeHtml(zr.zone_id)}')" style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;border:1px solid ${state.highlightedZoneId === zr.zone_id ? '#C81E1E' : '#E2E8F0'};border-radius:8px;cursor:pointer;background:${state.highlightedZoneId === zr.zone_id ? '#FEF2F2' : '#fff'};font-size:0.8rem;">
                <span style="font-weight:600;">${escapeHtml(zr.name.split('(')[0].trim())}</span>
                <span style="background:${badgeBg};color:${badgeColor};padding:2px 8px;border-radius:99px;font-size:0.72rem;font-weight:600;">${statusTxt}</span>
            </div>`;
        }).join('');

        const suggestionList = r.suggestions.map(s => `<li style="margin-bottom:4px;">${escapeHtml(s)}</li>`).join('');

        box.innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px;">
                <div style="background:#ECFDF5;border:1px solid #A7F3D0;border-radius:10px;padding:8px;text-align:center;">
                    <div style="font-size:1.1rem;font-weight:700;color:#065F46;">${r.coveredPct}%</div>
                    <div style="font-size:0.72rem;color:#047857;">ครอบคลุม (${r.coveredAreaM2} ตร.ม.)</div>
                </div>
                <div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:10px;padding:8px;text-align:center;">
                    <div style="font-size:1.1rem;font-weight:700;color:#991B1B;">${r.blindPct}%</div>
                    <div style="font-size:0.72rem;color:#B91C1C;">จุดอับ (${r.blindAreaM2} ตร.ม.)</div>
                </div>
                <div style="background:#EFF6FF;border:1px solid #BFDBFE;border-radius:10px;padding:8px;text-align:center;">
                    <div style="font-size:1.1rem;font-weight:700;color:#1E40AF;">${r.overlapPct}%</div>
                    <div style="font-size:0.72rem;color:#1D4ED8;">ซ้อนทับ (${r.overlapAreaM2} ตร.ม.)</div>
                </div>
            </div>
            <div style="font-size:0.8rem;font-weight:600;margin-bottom:6px;color:#334155;">สถานะแยกตามโซน (คลิกเพื่อเน้นบนแผนผัง):</div>
            <div style="display:grid;gap:6px;margin-bottom:12px;">${zoneRows || '<div style="font-size:0.8rem;color:var(--text-muted);">ไม่มีโซนที่เปิดใช้งาน</div>'}</div>
            <div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;padding:10px 12px;font-size:0.78rem;color:#78350F;line-height:1.5;">
                <strong><i class="fa-solid fa-triangle-exclamation"></i> ข้อเสนอแนะและจุดตรวจสอบ:</strong>
                <ul style="margin:4px 0 6px 16px;padding:0;">${suggestionList}</ul>
                <div style="font-size:0.72rem;color:#92400E;border-top:1px dashed #FCD34D;padding-top:5px;">
                    *การประเมินจุดอับคำนวณจากเรขาคณิต 2 มิติเบื้องต้น ยังไม่รวมเสา ชั้นวางสูง ผนังกั้น และแสงย้อน ซึ่งต้องตรวจสอบหน้างานจริงก่อนติดตั้ง
                </div>
            </div>
        `;
    }

    function renderSummaryDashboard() {
        const r = state.coverageReport || analyzeCoverage();
        const totalCams = state.cameras.length;
        const activeZones = state.zones.filter(z => z.active);
        const coveredZones = r.zoneResults.filter(z => z.status === 'covered').length;
        const pendingZones = r.zoneResults.filter(z => z.status !== 'covered').length;

        const analyticsSet = new Set();
        const resSet = new Set();
        state.cameras.forEach(c => {
            resSet.add(c.resolution);
            (c.analytics || []).forEach(a => analyticsSet.add(a));
        });

        const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        setEl('sumTotalCams', `${totalCams} ตัว`);
        setEl('sumCoveredZones', `${coveredZones} / ${activeZones.length} โซน`);
        setEl('sumPendingZones', `${pendingZones} โซน`);
        setEl('sumResolution', Array.from(resSet).join(', ') || 'Pending Verification');
        setEl('sumAnalyticsCount', `${analyticsSet.size} ประเภท`);

        const reasoningBox = document.getElementById('recommendationReasoning');
        if (reasoningBox) {
            reasoningBox.innerHTML = `
                <ul style="margin:0 0 6px 18px;padding:0;font-size:0.82rem;color:#334155;line-height:1.6;">
                    ${state.reasoning.map(line => `<li>${escapeHtml(line)}</li>`).join('')}
                </ul>
                <div style="font-size:0.76rem;color:#991B1B;font-weight:500;">
                    <i class="fa-solid fa-circle-info"></i> ผลลัพธ์นี้เป็นแผนเบื้องต้นจากการคำนวณพื้นที่ ต้องตรวจสอบสภาพหน้างานจริง (สิ่งกีดขวาง ระดับฝ้า แสงสว่าง) ก่อนติดตั้งทุกครั้ง
                </div>
            `;
        }

        const tbody = document.getElementById('summaryCameraTableBody');
        if (tbody) {
            if (!state.cameras.length) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:20px;">ยังไม่มีกล้องในแผนผัง กด "+ เพิ่มกล้อง" หรือ "คำนวณแนะนำอัตโนมัติ"</td></tr>';
            } else {
                tbody.innerHTML = state.cameras.map(cam => {
                    const isSel = cam.camera_id === state.selectedCameraId;
                    return `<tr onclick="CameraPlanner.selectCamera('${escapeHtml(cam.camera_id)}')" style="cursor:pointer;${isSel ? 'background:#FEF2F2;' : ''}">
                        <td><strong style="color:var(--red);">${escapeHtml(cam.camera_id)}</strong></td>
                        <td><strong>${escapeHtml(cam.name)}</strong></td>
                        <td>ชั้น ${cam.floor} · ${escapeHtml(cam.zone_name)}</td>
                        <td>X:${cam.x_m} ม., Y:${cam.y_m} ม.</td>
                        <td>${cam.direction_deg}° (${escapeHtml(cam.lens_label)})</td>
                        <td>${escapeHtml(cam.resolution)}</td>
                        <td>${(cam.analytics || []).map(a => `<span class="badge badge-customer" style="margin:1px;">${escapeHtml(a)}</span>`).join('')}</td>
                        <td><button type="button" class="btn-saas btn-saas-danger-soft" style="height:28px;padding:0 10px;font-size:0.75rem;" onclick="event.stopPropagation();CameraPlanner.deleteCamera('${escapeHtml(cam.camera_id)}')"><i class="fa-solid fa-trash-can"></i></button></td>
                    </tr>`;
                }).join('');
            }
        }
    }

    function renderFloorTabs() {
        const holder = document.getElementById('floorSelectorGroup');
        if (!holder) return;
        let html = '';
        for (let f = 1; f <= state.floors; f++) {
            html += `<button type="button" class="admin-tab-btn ${state.activeFloor === f ? 'active' : ''}" style="padding:5px 12px;font-size:0.78rem;" onclick="CameraPlanner.switchFloor(${f})">ชั้น ${f}</button>`;
        }
        holder.innerHTML = html;
    }

    function renderAll() {
        renderFloorTabs();
        renderCanvas();
        renderCameraDetailPanel();
        renderBlindSpotSummary();
        renderSummaryDashboard();
    }

    // ----------------------------------------------------------------------
    // 8. Cloud Firestore API Sync & Saved Plans Management
    // ----------------------------------------------------------------------
    function getAuthSession() {
        try { return JSON.parse(localStorage.getItem('currentUser') || '{}'); }
        catch { return {}; }
    }

    async function fetchSavedLayouts() {
        const listEl = document.getElementById('savedLayoutsSelect');
        const statusEl = document.getElementById('plannerSaveStatus');
        const session = getAuthSession();

        if (!session.access_token) {
            if (listEl) listEl.innerHTML = '<option value="">-- เข้าสู่ระบบเพื่อโหลดแผนผังจาก Cloud --</option>';
            return;
        }

        try {
            const res = await fetch(`${PLANNER_API}/api/camera-layouts`, {
                headers: { Authorization: `Bearer ${session.access_token}` }
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'โหลดรายการแผนผังไม่สำเร็จ');
            state.savedLayouts = data.data || [];

            if (listEl) {
                if (!state.savedLayouts.length) {
                    listEl.innerHTML = '<option value="">-- ยังไม่มีแผนผังที่บันทึกไว้ --</option>';
                } else {
                    listEl.innerHTML = '<option value="">-- เลือกแผนผังที่บันทึกไว้เพื่อแก้ไข --</option>' +
                        state.savedLayouts.map(item =>
                            `<option value="${escapeHtml(item.layout_id)}" ${state.layoutId === item.layout_id ? 'selected' : ''}>${escapeHtml(item.name)} (${item.width_m}×${item.length_m} ม. · ${(item.cameras || []).length} กล้อง)</option>`
                        ).join('');
                }
            }
        } catch (err) {
            if (statusEl) {
                statusEl.textContent = err.message || 'ไม่สามารถโหลดแผนผังจาก Cloud ได้';
                statusEl.style.color = '#DC2626';
            }
        }
    }

    async function saveLayoutToCloud() {
        const statusEl = document.getElementById('plannerSaveStatus');
        const saveBtn = document.getElementById('btnSaveLayout');
        const session = getAuthSession();

        const nameInput = document.getElementById('layoutNameInput');
        if (nameInput && nameInput.value.trim()) {
            state.layoutName = nameInput.value.trim();
        }

        const payload = {
            layout_id: state.layoutId,
            name: state.layoutName,
            business_type: state.businessType,
            width_m: state.widthM,
            length_m: state.lengthM,
            ceiling_height_m: state.ceilingHeightM,
            floors: state.floors,
            entrance_count: state.entranceCount,
            entrance_side: state.entranceSide,
            analytics_focus: state.analyticsFocus,
            zones: state.zones,
            cameras: state.cameras.map(({ _computedCoverageM2, _computedCoveragePct, ...clean }) => clean),
            coverage_summary: state.coverageReport ? {
                covered_pct: state.coverageReport.coveredPct,
                blind_spot_pct: state.coverageReport.blindPct,
                overlap_pct: state.coverageReport.overlapPct
            } : {},
            notes: state.reasoning.join(' | ')
        };

        if (!session.access_token) {
            localStorage.setItem('tatip_camera_layout_draft', JSON.stringify(payload));
            if (statusEl) {
                statusEl.textContent = 'บันทึกร่างไว้ในเบราว์เซอร์ชั่วคราว (เข้าสู่ระบบเพื่อบันทึกขึ้น Cloud Firestore)';
                statusEl.style.color = '#D97706';
            }
            return;
        }

        if (saveBtn) saveBtn.disabled = true;
        if (statusEl) {
            statusEl.textContent = 'กำลังบันทึกแผนผังไปยัง Cloud Firestore...';
            statusEl.style.color = '#0284C7';
        }

        try {
            const res = await fetch(`${PLANNER_API}/api/camera-layouts`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${session.access_token}`
                },
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.status !== 'success') {
                throw new Error(data.detail || 'บันทึกแผนผังไม่สำเร็จ');
            }
            state.layoutId = data.layout_id;
            if (statusEl) {
                statusEl.textContent = `บันทึกสำเร็จ! รหัสแผนผัง: ${data.layout_id}`;
                statusEl.style.color = '#16A34A';
            }
            await fetchSavedLayouts();
        } catch (err) {
            if (statusEl) {
                statusEl.textContent = err.message || 'เกิดข้อผิดพลาดในการบันทึก';
                statusEl.style.color = '#DC2626';
            }
        } finally {
            if (saveBtn) saveBtn.disabled = false;
        }
    }

    async function loadSavedLayout(layoutId) {
        if (!layoutId) return;
        const found = state.savedLayouts.find(item => item.layout_id === layoutId);
        if (!found) return;

        state.layoutId = found.layout_id;
        state.layoutName = found.name || 'แผนผังติดตั้งกล้อง';
        state.businessType = found.business_type || 'restaurant';
        state.widthM = Number(found.width_m) || 10;
        state.lengthM = Number(found.length_m) || 12;
        state.ceilingHeightM = found.ceiling_height_m ?? null;
        state.floors = Number(found.floors) || 1;
        state.entranceCount = Number(found.entrance_count) || 1;
        state.entranceSide = found.entrance_side || 'front';
        state.analyticsFocus = found.analytics_focus || [];
        state.zones = found.zones || [];
        state.cameras = found.cameras || [];
        state.activeFloor = 1;
        state.selectedCameraId = state.cameras[0]?.camera_id || null;
        state.reasoning = found.notes ? found.notes.split(' | ') : ['โหลดแผนผังที่บันทึกไว้เรียบร้อยแล้ว'];

        syncInputsFromState();
        analyzeCoverage();
        renderAll();

        const statusEl = document.getElementById('plannerSaveStatus');
        if (statusEl) {
            statusEl.textContent = `โหลดแผนผัง "${state.layoutName}" (${state.layoutId}) เรียบร้อย`;
            statusEl.style.color = '#0284C7';
        }
    }

    async function deleteCurrentSavedLayout() {
        if (!state.layoutId) {
            alert('ยังไม่ได้เลือกแผนผังที่บันทึกบน Cloud');
            return;
        }
        const session = getAuthSession();
        if (!session.access_token) return;
        if (!confirm(`ต้องการลบแผนผัง "${state.layoutName}" (${state.layoutId}) ใช่หรือไม่?`)) return;

        try {
            const res = await fetch(`${PLANNER_API}/api/camera-layouts/${encodeURIComponent(state.layoutId)}`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${session.access_token}` }
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'ลบแผนผังไม่สำเร็จ');
            state.layoutId = null;
            const statusEl = document.getElementById('plannerSaveStatus');
            if (statusEl) {
                statusEl.textContent = 'ลบแผนผังออกจากระบบแล้ว';
                statusEl.style.color = '#16A34A';
            }
            await fetchSavedLayouts();
        } catch (err) {
            alert(err.message || 'ลบแผนผังไม่สำเร็จ');
        }
    }

    // ----------------------------------------------------------------------
    // 9. Public Interactive Handlers & Initialization
    // ----------------------------------------------------------------------
    function syncInputsFromState() {
        const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        const setChk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };

        setVal('layoutNameInput', state.layoutName);
        setVal('businessTypeSelect', state.businessType);
        setVal('spaceWidthInput', state.widthM);
        setVal('spaceLengthInput', state.lengthM);
        setVal('ceilingHeightInput', state.ceilingHeightM ?? '');
        setVal('floorCountInput', state.floors);
        setVal('entranceCountInput', state.entranceCount);
        setVal('entranceSideSelect', state.entranceSide);

        ['entrance', 'cashier', 'service', 'seating', 'kitchen'].forEach(z => {
            setChk(`zoneToggle_${z}`, state.zonesEnabled[z]);
        });
        document.querySelectorAll('input[name="analyticsFocus"]').forEach(cb => {
            cb.checked = state.analyticsFocus.includes(cb.value);
        });
    }

    function readInputsToState() {
        const getVal = id => document.getElementById(id)?.value;
        state.layoutName = (getVal('layoutNameInput') || 'แผนผังติดตั้งกล้อง TATIP AI').trim();
        state.businessType = getVal('businessTypeSelect') || 'restaurant';
        state.widthM = clamp(parseFloat(getVal('spaceWidthInput')) || 10, 3, 100);
        state.lengthM = clamp(parseFloat(getVal('spaceLengthInput')) || 12, 3, 100);
        const ceilRaw = parseFloat(getVal('ceilingHeightInput'));
        state.ceilingHeightM = !isNaN(ceilRaw) && ceilRaw >= 1.8 ? clamp(ceilRaw, 1.8, 30) : null;
        state.floors = Math.round(clamp(parseInt(getVal('floorCountInput'), 10) || 1, 1, 5));
        state.entranceCount = Math.round(clamp(parseInt(getVal('entranceCountInput'), 10) || 1, 1, 4));
        state.entranceSide = getVal('entranceSideSelect') || 'front';

        ['entrance', 'cashier', 'service', 'seating', 'kitchen'].forEach(z => {
            const cb = document.getElementById(`zoneToggle_${z}`);
            if (cb) state.zonesEnabled[z] = cb.checked;
        });

        state.analyticsFocus = Array.from(document.querySelectorAll('input[name="analyticsFocus"]:checked'))
            .map(cb => cb.value);
    }

    function selectCamera(camId) {
        state.selectedCameraId = camId;
        renderCanvas();
        renderCameraDetailPanel();
        renderSummaryDashboard();
    }

    function addCustomCamera() {
        const nextNum = state.cameras.reduce((max, c) => {
            const n = parseInt(c.camera_id.replace(/\D/g, ''), 10) || 0;
            return Math.max(max, n);
        }, 0) + 1;

        const newCam = createCameraObject(nextNum, {
            name: `Custom Cam ${nextNum}`,
            floor: state.activeFloor,
            zone_name: 'กำหนดเอง (ลากเพื่อวางตำแหน่ง)',
            x_m: +(state.widthM / 2).toFixed(2),
            y_m: +(state.lengthM / 2).toFixed(2),
            direction_deg: 90,
            camera_type: 'standard_4mp',
            is_custom: true
        });

        state.cameras.push(newCam);
        state.selectedCameraId = newCam.camera_id;
        analyzeCoverage();
        renderAll();
    }

    function deleteCamera(camId) {
        state.cameras = state.cameras.filter(c => c.camera_id !== camId);
        if (state.selectedCameraId === camId) {
            const sameFloor = state.cameras.find(c => c.floor === state.activeFloor) || state.cameras[0];
            state.selectedCameraId = sameFloor ? sameFloor.camera_id : null;
        }
        analyzeCoverage();
        renderAll();
    }

    window.CameraPlanner = {
        init() {
            const params = new URLSearchParams(window.location.search);
            const bParam = params.get('business');
            if (bParam && BUSINESS_TEMPLATES[bParam]) {
                state.businessType = bParam;
                state.widthM = BUSINESS_TEMPLATES[bParam].defaultWidth;
                state.lengthM = BUSINESS_TEMPLATES[bParam].defaultLength;
            }
            syncInputsFromState();
            generateRecommendedPlan();
            fetchSavedLayouts();
        },
        onBusinessTypeChange(newType) {
            if (BUSINESS_TEMPLATES[newType]) {
                state.businessType = newType;
                state.widthM = BUSINESS_TEMPLATES[newType].defaultWidth;
                state.lengthM = BUSINESS_TEMPLATES[newType].defaultLength;
                syncInputsFromState();
                generateRecommendedPlan();
            }
        },
        recalculate() {
            state.layoutId = null; // New recommendation plan unless saving over existing
            readInputsToState();
            generateRecommendedPlan();
        },
        setViewMode(mode) {
            state.viewMode = mode === '3d' ? '3d' : '2d';
            renderCanvas();
        },
        switchFloor(floor) {
            state.activeFloor = floor;
            const onFloor = state.cameras.find(c => c.floor === floor);
            state.selectedCameraId = onFloor ? onFloor.camera_id : null;
            analyzeCoverage();
            renderAll();
        },
        toggleOverlay(key, checked) {
            state.overlays[key] = !!checked;
            renderCanvas();
        },
        toggleZoneHighlight(zoneId) {
            state.highlightedZoneId = state.highlightedZoneId === zoneId ? null : zoneId;
            renderCanvas();
            renderBlindSpotSummary();
        },
        selectCamera,
        addCustomCamera,
        deleteCamera,
        updateSelectedCameraField(field, value) {
            const cam = state.cameras.find(c => c.camera_id === state.selectedCameraId);
            if (!cam) return;
            cam[field] = value;
            renderCanvas();
            renderSummaryDashboard();
        },
        updateSelectedCameraDirection(deg) {
            const cam = state.cameras.find(c => c.camera_id === state.selectedCameraId);
            if (!cam) return;
            cam.direction_deg = normalizeAngle(parseFloat(deg) || 0);
            analyzeCoverage();
            renderCanvas();
            renderCameraDetailPanel();
            renderBlindSpotSummary();
            renderSummaryDashboard();
        },
        updateSelectedCameraZone(zoneId) {
            const cam = state.cameras.find(c => c.camera_id === state.selectedCameraId);
            if (!cam) return;
            const z = state.zones.find(item => item.zone_id === zoneId);
            cam.zone_id = z ? z.zone_id : '';
            cam.zone_name = z ? z.name : 'พื้นที่ทั่วไป';
            renderSummaryDashboard();
        },
        updateSelectedCameraPreset(presetKey) {
            const cam = state.cameras.find(c => c.camera_id === state.selectedCameraId);
            const preset = CAMERA_PRESETS[presetKey];
            if (!cam || !preset) return;
            cam.camera_type = presetKey;
            cam.resolution = preset.resolution;
            cam.lens_label = preset.lens_label;
            cam.fov_deg = preset.fov_deg;
            cam.range_m = preset.range_m;
            cam.detection_distance = preset.detection_distance;
            cam.analytics = [...preset.default_analytics];
            analyzeCoverage();
            renderAll();
        },
        saveLayoutToCloud,
        loadSavedLayout,
        deleteCurrentSavedLayout
    };

    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('plannerCanvasContainer')) {
            window.CameraPlanner.init();
        }
    });
})();

