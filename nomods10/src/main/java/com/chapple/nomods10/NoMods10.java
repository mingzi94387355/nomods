package com.chapple.nomods10;

import com.mojang.logging.LogUtils;
import net.minecraft.server.level.ServerPlayer;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.network.PacketDistributor;
import net.neoforged.neoforge.network.event.RegisterPayloadHandlersEvent;
import net.neoforged.neoforge.network.registration.PayloadRegistrar;
import org.slf4j.Logger;

/**
 * nomods10 - registers a network channel, sends one hello packet when a player
 * joins, and does nothing else.
 */
@Mod(NoMods10.MOD_ID)
public final class NoMods10 {
    public static final String MOD_ID = "nomods10";
    public static final String PROTOCOL_VERSION = "1";
    public static final Logger LOGGER = LogUtils.getLogger();

    public NoMods10(IEventBus modEventBus, ModContainer modContainer) {
        modEventBus.addListener(NoMods10::registerPayloads);
        NeoForge.EVENT_BUS.addListener(NoMods10::onPlayerLoggedIn);
    }

    private static void registerPayloads(final RegisterPayloadHandlersEvent event) {
        // One optional, bidirectional channel carrying a single hello message.
        final PayloadRegistrar registrar = event.registrar(PROTOCOL_VERSION).optional();
        registrar.playBidirectional(HelloPayload.TYPE, HelloPayload.STREAM_CODEC, HelloPayload::handle);
    }

    private static void onPlayerLoggedIn(final PlayerEvent.PlayerLoggedInEvent event) {
        // Only greet clients that actually negotiated this mod's channel, so a
        // client with fewer mods never receives a payload it cannot decode.
        if (event.getEntity() instanceof ServerPlayer player && player.connection.hasChannel(HelloPayload.TYPE)) {
            PacketDistributor.sendToPlayer(player, new HelloPayload("hello from " + MOD_ID));
        }
    }
}
