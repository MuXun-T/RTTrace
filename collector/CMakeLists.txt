add_library(trace_collector STATIC
  core/trace_collector.cpp
)

target_include_directories(trace_collector
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(trace_collector PUBLIC cxx_std_17)

add_executable(trace_sim
  hook/trace_sim.cpp
)

target_link_libraries(trace_sim PRIVATE trace_collector)
