cmake_minimum_required(VERSION 3.22)
project(localflow_native C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(TP ${CMAKE_CURRENT_SOURCE_DIR}/../../../../third_party)
set(LLAMA_DIR ${TP}/llama.cpp)
set(WHISPER_DIR ${TP}/whisper.cpp)

# One ggml for both engines (llama.cpp's); whisper.cpp's single source file is
# compiled against it. Static libs, hidden symbols: one small .so in the APK.
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_BACKEND_DL OFF CACHE BOOL "" FORCE)
if(ANDROID_ABI STREQUAL "arm64-v8a")
    # dotprod + fp16 + i8mm: every phone from ~2021 on. The Kotlin side checks
    # /proc/cpuinfo and refuses to load the engine on a CPU without them.
    set(GGML_CPU_ARM_ARCH "armv8.2-a+dotprod+fp16+i8mm" CACHE STRING "" FORCE)
endif()

add_subdirectory(${LLAMA_DIR} llama_build EXCLUDE_FROM_ALL)

add_library(whisper_core STATIC ${WHISPER_DIR}/src/whisper.cpp ${WHISPER_DIR}/src/aneforge/whisper-aneforge.cpp)
target_include_directories(whisper_core PUBLIC ${WHISPER_DIR}/include ${WHISPER_DIR}/src)
target_link_libraries(whisper_core PUBLIC ggml)
target_compile_options(whisper_core PRIVATE -O3 -fvisibility=hidden)
target_compile_definitions(whisper_core PRIVATE WHISPER_VERSION="1.9.5")

add_library(lflow SHARED jni_whisper.cpp jni_llama.cpp)
target_include_directories(lflow PRIVATE ${LLAMA_DIR}/include ${LLAMA_DIR}/ggml/include ${WHISPER_DIR}/include)
target_link_libraries(lflow PRIVATE whisper_core llama ggml log android)
target_link_options(lflow PRIVATE -Wl,--gc-sections -Wl,--exclude-libs,ALL -Wl,-z,max-page-size=16384)
