import sys
import logging
from utils import run_hidden_subprocess

def try_pytorch_detection():
    """ Try PyTorch detection with detailed diagnostics """
    try:
        import torch
        
        cuda_available = torch.cuda.is_available()
        
        if cuda_available:
            device_count = torch.cuda.device_count()
            if device_count > 0:
                gpu_name = torch.cuda.get_device_name(0)
                props = torch.cuda.get_device_properties(0)
                memory_gb = props.total_memory / (1024**3)
                cuda_version = torch.version.cuda
                
                return {
                    "success": True,
                    "gpu_info": {
                        "has_cuda": True,
                        "gpu_memory_gb": memory_gb,
                        "gpu_name": gpu_name,
                        "cuda_version": cuda_version,
                        "recommended_device": "cuda",
                        "detection_method": "pytorch"
                    }
                }
        
        return {
            "success": False,
            "error": f"PyTorch installed but CUDA not available (cuda_available={cuda_available})"
        }
        
    except ImportError:
        return {
            "success": False,
            "error": "PyTorch not installed"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"PyTorch error: {str(e)}"
        }


def try_nvml_detection():
    """ Try NVIDIA-ML library detection """
    try:
        import pynvml
        pynvml.nvmlInit()
        
        device_count = pynvml.nvmlDeviceGetCount()
        if device_count > 0:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode('utf-8')
            memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            memory_gb = memory_info.total / (1024**3)
            
            try:
                cuda_version = pynvml.nvmlSystemGetCudaDriverVersion()
                cuda_version_str = f"{cuda_version // 1000}.{(cuda_version % 1000) // 10}"
            except:
                cuda_version_str = "Available"
            
            return {
                "success": True,
                "gpu_info": {
                    "has_cuda": True,
                    "gpu_memory_gb": memory_gb,
                    "gpu_name": name,
                    "cuda_version": cuda_version_str,
                    "recommended_device": "cuda",
                    "detection_method": "nvml"
                }
            }
        
        return {
            "success": False,
            "error": "NVML: No GPUs found"
        }
        
    except ImportError:
        return {
            "success": False,
            "error": "nvidia-ml-py not installed"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"NVML error: {str(e)}"
        }


def try_nvidia_smi_detection():
    """ Try nvidia-smi command-line detection """
    try:
        result = run_hidden_subprocess(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'], 
                              capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            if lines:
                parts = lines[0].split(', ')
                if len(parts) >= 2:
                    gpu_name = parts[0].strip()
                    memory_mb = float(parts[1].strip())
                    memory_gb = memory_mb / 1024
                    
                    return {
                        "success": True,
                        "gpu_info": {
                            "has_cuda": True,
                            "gpu_memory_gb": memory_gb,
                            "gpu_name": gpu_name,
                            "cuda_version": "Available",
                            "recommended_device": "cuda",
                            "detection_method": "nvidia-smi"
                        }
                    }
        
        return {
            "success": False,
            "error": f"nvidia-smi failed: {result.stderr or 'No output'}"
        }
        
    except FileNotFoundError:
        return {
            "success": False,
            "error": "nvidia-smi command not found"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"nvidia-smi error: {str(e)}"
        }


def try_platform_detection():
    """ Try platform-specific GPU detection """
    try:
        if sys.platform == "win32":
            try:
                result = run_hidden_subprocess(['wmic', 'path', 'win32_VideoController', 'get', 'name'], 
                                      capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and 'nvidia' in result.stdout.lower():
                    lines = result.stdout.strip().split('\n')
                    for line in lines:
                        if line.strip() and 'nvidia' in line.lower():
                            gpu_name = line.strip()
                            return {
                                "success": True,
                                "gpu_info": {
                                    "has_cuda": True,
                                    "gpu_memory_gb": 4.0,
                                    "gpu_name": gpu_name,
                                    "cuda_version": "Unknown",
                                    "recommended_device": "cuda",
                                    "detection_method": "windows-wmic"
                                }
                            }
            except:
                pass
        
        return {
            "success": False,
            "error": "No platform-specific detection available"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Platform detection error: {str(e)}"
        }


def try_intel_gpu_detection():
    """ Detect Intel integrated and dedicated GPUs """
    try:
        if sys.platform == "win32":
            result = run_hidden_subprocess(['wmic', 'path', 'win32_VideoController', 'where', 
                                   'name like "%Intel%"', 'get', 'name,AdapterRAM'], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                for line in lines[1:]:
                    if line.strip() and 'intel' in line.lower():
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            gpu_name = ' '.join(parts[:-1]) if len(parts) > 1 else line.strip()
                            try:
                                memory_bytes = int(parts[-1]) if parts[-1].isdigit() else 0
                                memory_gb = memory_bytes / (1024**3) if memory_bytes > 0 else 2.0
                            except:
                                memory_gb = 2.0
                            
                            return {
                                "success": True,
                                "gpu_info": {
                                    "has_cuda": False,
                                    "gpu_memory_gb": memory_gb,
                                    "gpu_name": gpu_name,
                                    "cuda_version": None,
                                    "recommended_device": "cpu",
                                    "detection_method": "intel-wmic",
                                    "gpu_vendor": "intel"
                                }
                            }
        
        elif sys.platform == "linux":
            result = run_hidden_subprocess(['lspci', '-nn'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.lower()
                if 'intel' in lines and ('vga' in lines or '3d' in lines):
                    for line in result.stdout.split('\n'):
                        if 'intel' in line.lower() and ('vga' in line.lower() or '3d' in line.lower()):
                            gpu_name = line.split(': ')[-1].strip() if ': ' in line else "Intel Graphics"
                            return {
                                "success": True,
                                "gpu_info": {
                                    "has_cuda": False,
                                    "gpu_memory_gb": 2.0,
                                    "gpu_name": gpu_name,
                                    "cuda_version": None,
                                    "recommended_device": "cpu",
                                    "detection_method": "intel-lspci",
                                    "gpu_vendor": "intel"
                                }
                            }
        
        return {
            "success": False,
            "error": "No Intel GPU found"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Intel detection error: {str(e)}"
        }


def try_amd_gpu_detection():
    """ Detect AMD integrated and dedicated GPUs """
    try:
        if sys.platform == "win32":
            result = run_hidden_subprocess(['wmic', 'path', 'win32_VideoController', 'where', 
                                   'name like "%AMD%" or name like "%Radeon%"', 
                                   'get', 'name,AdapterRAM'], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                for line in lines[1:]:
                    if line.strip() and ('amd' in line.lower() or 'radeon' in line.lower()):
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            gpu_name = ' '.join(parts[:-1]) if len(parts) > 1 else line.strip()
                            try:
                                memory_bytes = int(parts[-1]) if parts[-1].isdigit() else 0
                                memory_gb = memory_bytes / (1024**3) if memory_bytes > 0 else 4.0
                            except:
                                memory_gb = 4.0
                            
                            return {
                                "success": True,
                                "gpu_info": {
                                    "has_cuda": False,
                                    "gpu_memory_gb": memory_gb,
                                    "gpu_name": gpu_name,
                                    "cuda_version": None,
                                    "recommended_device": "cpu",
                                    "detection_method": "amd-wmic",
                                    "gpu_vendor": "amd"
                                }
                            }
        
        elif sys.platform == "linux":
            result = run_hidden_subprocess(['lspci', '-nn'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.lower()
                if ('amd' in lines or 'radeon' in lines) and ('vga' in lines or '3d' in lines):
                    for line in result.stdout.split('\n'):
                        if ('amd' in line.lower() or 'radeon' in line.lower()) and ('vga' in line.lower() or '3d' in line.lower()):
                            gpu_name = line.split(': ')[-1].strip() if ': ' in line else "AMD Radeon Graphics"
                            return {
                                "success": True,
                                "gpu_info": {
                                    "has_cuda": False,
                                    "gpu_memory_gb": 4.0,
                                    "gpu_name": gpu_name,
                                    "cuda_version": None,
                                    "recommended_device": "cpu",
                                    "detection_method": "amd-lspci",
                                    "gpu_vendor": "amd"
                                }
                            }
        
        return {
            "success": False,
            "error": "No AMD GPU found"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"AMD detection error: {str(e)}"
        }


def try_universal_gpu_detection():
    """ Universal GPU detection using platform APIs """
    try:
        gpu_list = []
        
        if sys.platform == "win32":
            result = run_hidden_subprocess(['wmic', 'path', 'win32_VideoController', 
                                   'get', 'name,AdapterRAM'], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                for line in lines[1:]:
                    if line.strip():
                        parts = line.strip().split()
                        if len(parts) >= 1:
                            gpu_name = ' '.join(parts[:-1]) if len(parts) > 1 else parts[0]
                            try:
                                memory_bytes = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else 0
                                memory_gb = memory_bytes / (1024**3) if memory_bytes > 0 else 2.0
                            except:
                                memory_gb = 2.0
                            
                            vendor = "unknown"
                            has_cuda = False
                            if 'nvidia' in gpu_name.lower():
                                vendor = "nvidia"
                                has_cuda = True
                            elif 'intel' in gpu_name.lower():
                                vendor = "intel"
                            elif 'amd' in gpu_name.lower() or 'radeon' in gpu_name.lower():
                                vendor = "amd"
                            
                            gpu_list.append({
                                "has_cuda": has_cuda,
                                "gpu_memory_gb": memory_gb,
                                "gpu_name": gpu_name,
                                "cuda_version": "Available" if has_cuda else None,
                                "recommended_device": "cuda" if has_cuda else "cpu",
                                "detection_method": "universal-wmic",
                                "gpu_vendor": vendor
                            })
        
        elif sys.platform == "linux":
            result = run_hidden_subprocess(['lspci', '-nn'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'vga' in line.lower() or '3d' in line.lower():
                        gpu_name = line.split(': ')[-1].strip() if ': ' in line else "Graphics Device"
                        
                        vendor = "unknown"
                        has_cuda = False
                        memory_gb = 2.0
                        
                        if 'nvidia' in gpu_name.lower():
                            vendor = "nvidia"
                            has_cuda = True
                            memory_gb = 4.0
                        elif 'intel' in gpu_name.lower():
                            vendor = "intel"
                            memory_gb = 2.0
                        elif 'amd' in gpu_name.lower() or 'radeon' in gpu_name.lower():
                            vendor = "amd"
                            memory_gb = 4.0
                        
                        gpu_list.append({
                            "has_cuda": has_cuda,
                            "gpu_memory_gb": memory_gb,
                            "gpu_name": gpu_name,
                            "cuda_version": "Available" if has_cuda else None,
                            "recommended_device": "cuda" if has_cuda else "cpu",
                            "detection_method": "universal-lspci",
                            "gpu_vendor": vendor
                        })
        
        if gpu_list:
            return {
                "success": True,
                "gpu_list": gpu_list
            }
        else:
            return {
                "success": False,
                "error": "No GPUs found via universal detection"
            }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Universal detection error: {str(e)}"
        }


def assess_gpu_capabilities(gpu_info):
    """ Assess GPU capabilities for AI workloads """
    gpu_name = gpu_info["gpu_name"].lower()
    memory_gb = gpu_info.get("gpu_memory_gb", 0)
    vendor = gpu_info.get("gpu_vendor", "unknown")
    
    if vendor == "nvidia" or "nvidia" in gpu_name or "geforce" in gpu_name or "rtx" in gpu_name:
        if any(x in gpu_name for x in ["rtx 50", "rtx 40"]):
            return {
                "tier": "high-end", 
                "ai_capable": True, 
                "recommended_device": "cuda",
                "recommended_model": "large-v2",
                "recommended_compute": "float16"
            }
        elif any(x in gpu_name for x in ["rtx 30", "rtx 20"]):
            return {
                "tier": "mid-high", 
                "ai_capable": True, 
                "recommended_device": "cuda",
                "recommended_model": "large-v2" if memory_gb >= 6 else "medium",
                "recommended_compute": "int8_float16"
            }
        elif "gtx" in gpu_name:
            return {
                "tier": "legacy", 
                "ai_capable": True, 
                "recommended_device": "cuda",
                "recommended_model": "medium",
                "recommended_compute": "int8"
            }
    
    elif vendor == "intel" or "intel" in gpu_name:
        if "arc" in gpu_name:
            return {
                "tier": "mid-range", 
                "ai_capable": False, 
                "recommended_device": "cpu",
                "recommended_model": "medium",
                "recommended_compute": "int8"
            }
        elif any(x in gpu_name for x in ["iris xe", "iris plus"]):
            return {
                "tier": "integrated-good", 
                "ai_capable": False, 
                "recommended_device": "cpu",
                "recommended_model": "base",
                "recommended_compute": "int8"
            }
        else:  # Basic integrated (UHD, HD)
            return {
                "tier": "integrated-basic", 
                "ai_capable": False, 
                "recommended_device": "cpu",
                "recommended_model": "base",
                "recommended_compute": "int8"
            }
    
    elif vendor == "amd" or "amd" in gpu_name or "radeon" in gpu_name:
        if any(x in gpu_name for x in ["rx 7000", "rx 6000"]):
            return {
                "tier": "high-end", 
                "ai_capable": False, 
                "recommended_device": "cpu",
                "recommended_model": "large-v2",  # Usually paired with good CPU
                "recommended_compute": "int8"
            }
        elif any(x in gpu_name for x in ["rx 5000", "rx 500"]):
            return {
                "tier": "mid-range", 
                "ai_capable": False, 
                "recommended_device": "cpu",
                "recommended_model": "medium",
                "recommended_compute": "int8"
            }
        else:  # Integrated or older
            return {
                "tier": "integrated-good", 
                "ai_capable": False, 
                "recommended_device": "cpu",
                "recommended_model": "base",
                "recommended_compute": "int8"
            }
    
    # Unknown GPU
    return {
        "tier": "unknown", 
        "ai_capable": False, 
        "recommended_device": "cpu",
        "recommended_model": "base",
        "recommended_compute": "int8"
    }


def select_best_gpu_for_ai(gpu_list):
    """ Select the best GPU for AI workloads from detected GPUs """
    if not gpu_list:
        return {
            "has_cuda": False,
            "gpu_memory_gb": 0,
            "gpu_name": "None",
            "cuda_version": None,
            "recommended_device": "cpu",
            "detection_method": "none",
            "detection_details": ["No GPUs detected"]
        }
    
    # Priority: CUDA-capable > High memory > Dedicated > Integrated
    best_gpu = None
    best_score = -1
    
    for gpu in gpu_list:
        score = 0
        capabilities = assess_gpu_capabilities(gpu)
        
        if gpu.get("has_cuda", False):
            score += 1000
        
        score += gpu.get("gpu_memory_gb", 0) * 10
        
        tier_scores = {
            "high-end": 100,
            "mid-high": 80,
            "mid-range": 60,
            "legacy": 40,
            "integrated-good": 20,
            "integrated-basic": 10,
            "unknown": 5
        }
        score += tier_scores.get(capabilities["tier"], 0)
        
        if score > best_score:
            best_score = score
            best_gpu = gpu
    
    if best_gpu:
        capabilities = assess_gpu_capabilities(best_gpu)
        best_gpu.update(capabilities)
    
    return best_gpu

def detect_gpu_info():
    """ Comprehensive multi-vendor GPU detection with intelligent selection """
    all_detected_gpus = []
    detection_details = []
    
    logging.info("Phase 1: Trying NVIDIA GPU detection methods...")
    
    logging.info("Trying PyTorch GPU detection...")
    pytorch_result = try_pytorch_detection()
    if pytorch_result["success"]:
        logging.info(f"NVIDIA GPU detected via PyTorch: {pytorch_result['gpu_info']['gpu_name']}")
        all_detected_gpus.append(pytorch_result["gpu_info"])
    else:
        detection_details.append(f"PyTorch: {pytorch_result['error']}")
        logging.info(f"PyTorch detection failed: {pytorch_result['error']}")
    
    if not all_detected_gpus:
        logging.info("Trying NVML GPU detection...")
        nvml_result = try_nvml_detection()
        if nvml_result["success"]:
            logging.info(f"NVIDIA GPU detected via NVML: {nvml_result['gpu_info']['gpu_name']}")
            all_detected_gpus.append(nvml_result["gpu_info"])
        else:
            detection_details.append(f"NVML: {nvml_result['error']}")
            logging.info(f"NVML detection failed: {nvml_result['error']}")
    
    if not all_detected_gpus:
        logging.info("Trying nvidia-smi GPU detection...")
        smi_result = try_nvidia_smi_detection()
        if smi_result["success"]:
            logging.info(f"NVIDIA GPU detected via nvidia-smi: {smi_result['gpu_info']['gpu_name']}")
            all_detected_gpus.append(smi_result['gpu_info'])
        else:
            detection_details.append(f"nvidia-smi: {smi_result['error']}")
            logging.info(f"nvidia-smi detection failed: {smi_result['error']}")
    
    if not all_detected_gpus:
        logging.info("Phase 2: Trying Intel GPU detection...")
        intel_result = try_intel_gpu_detection()
        if intel_result["success"]:
            logging.info(f"Intel GPU detected: {intel_result['gpu_info']['gpu_name']}")
            all_detected_gpus.append(intel_result["gpu_info"])
        else:
            detection_details.append(f"Intel: {intel_result['error']}")
            logging.info(f"Intel detection failed: {intel_result['error']}")
    
    if not all_detected_gpus:
        logging.info("Phase 3: Trying AMD GPU detection...")
        amd_result = try_amd_gpu_detection()
        if amd_result["success"]:
            logging.info(f"AMD GPU detected: {amd_result['gpu_info']['gpu_name']}")
            all_detected_gpus.append(amd_result["gpu_info"])
        else:
            detection_details.append(f"AMD: {amd_result['error']}")
            logging.info(f"AMD detection failed: {amd_result['error']}")
    
    # Phase 4: Universal Detection (fallback for any remaining GPUs)
    if not all_detected_gpus:
        logging.info("Phase 4: Trying universal GPU detection...")
        universal_result = try_universal_gpu_detection()
        if universal_result["success"]:
            logging.info(f"GPUs detected via universal method: {len(universal_result['gpu_list'])} GPU(s)")
            all_detected_gpus.extend(universal_result["gpu_list"])
        else:
            detection_details.append(f"Universal: {universal_result['error']}")
            logging.info(f"Universal detection failed: {universal_result['error']}")
    
    if all_detected_gpus:
        best_gpu = select_best_gpu_for_ai(all_detected_gpus)
        if best_gpu:
            best_gpu["detection_details"] = detection_details
            logging.info(f"Selected best GPU: {best_gpu['gpu_name']} (method: {best_gpu.get('detection_method', 'unknown')})")
            return best_gpu
    
    logging.warning("No GPUs detected by any method")
    return {
        "has_cuda": False,
        "gpu_memory_gb": 0,
        "gpu_name": "None",
        "cuda_version": None,
        "recommended_device": "cpu",
        "detection_method": "none",
        "detection_details": detection_details
    }

def detect_system_info():
    """ Detect system RAM and CPU info """
    system_info = {
        "ram_gb": 8,  # fallback
        "cpu_cores": 4,  # fallback
        "os_platform": sys.platform
    }
    
    try:
        import psutil
        system_info["ram_gb"] = psutil.virtual_memory().total / (1024**3)
        system_info["cpu_cores"] = psutil.cpu_count(logical=False) or psutil.cpu_count()
    except ImportError:
        logging.info("psutil not available for system detection, using fallback values")
        try:
            import multiprocessing
            system_info["cpu_cores"] = multiprocessing.cpu_count()
        except:
            pass
    except Exception as e:
        logging.warning(f"Error detecting system info: {e}")
    
    return system_info

def detect_hardware_capabilities():
    """ Comprehensive hardware detection """
    gpu_info = detect_gpu_info()
    system_info = detect_system_info()
    
    import time
    return {
        **gpu_info,
        **system_info,
        "detection_timestamp": time.time()
    }

def get_recommended_settings(hardware_info):
    """ Generate optimal settings based on hardware """
    recommendations = {}
    
    if hardware_info["has_cuda"] and hardware_info["gpu_memory_gb"] >= 4:
        recommendations["device"] = "cuda"
    else:
        recommendations["device"] = "cpu"
    
    if hardware_info["has_cuda"]:
        if hardware_info["gpu_memory_gb"] >= 7.5:
            recommendations["compute_type"] = "float16"  # Best quality; 7.5 threshold avoids rounding-edge GPUs (e.g. 8 GB cards reporting 7.9)
        elif hardware_info["gpu_memory_gb"] >= 4:
            recommendations["compute_type"] = "int8_float16"
        else:
            recommendations["compute_type"] = "int8"  # Memory limited
    else:
        if hardware_info["ram_gb"] >= 16:
            recommendations["compute_type"] = "int8"  # Faster on CPU
        else:
            recommendations["compute_type"] = "int8"  # Memory conservative
    
    if hardware_info["has_cuda"] and hardware_info["gpu_memory_gb"] >= 6:
        recommendations["model"] = "large-v2"  # BEST quality model
    elif hardware_info["has_cuda"] and hardware_info["gpu_memory_gb"] >= 4:
        recommendations["model"] = "medium"
    elif hardware_info["ram_gb"] >= 8:
        recommendations["model"] = "medium"
    else:
        recommendations["model"] = "base"     # Memory-limited systems
    
    if hardware_info["gpu_memory_gb"] >= 8 or hardware_info["ram_gb"] >= 16:
        recommendations["beam_size"] = 5  # Default
    else:
        recommendations["beam_size"] = 3  # More memory efficient
    
    if hardware_info["has_cuda"] and hardware_info["gpu_memory_gb"] >= 4:
        recommendations["vad_method"] = "pyannote_v3"  # Best accuracy with CUDA
    else:
        recommendations["vad_method"] = "silero_v4_fw"  # CPU friendly
    
    return recommendations
