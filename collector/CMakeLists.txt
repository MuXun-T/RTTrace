add_library(trace_collector STATIC
  core/trace_collector.cpp
)

add_library(rtd_p4_target_contract STATIC
  target/freertos_stm32f103/trace_target_contract.cpp
  hook/freertos/trace_freertos_hooks.cpp
)

target_include_directories(rtd_p4_target_contract
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/target/freertos_stm32f103
    ${CMAKE_CURRENT_SOURCE_DIR}/hook/freertos
)
target_compile_features(rtd_p4_target_contract PUBLIC cxx_std_17)

target_include_directories(trace_collector
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(trace_collector PUBLIC cxx_std_17)

add_executable(trace_sim
  hook/trace_sim.cpp
)

target_link_libraries(trace_sim PRIVATE trace_collector)
